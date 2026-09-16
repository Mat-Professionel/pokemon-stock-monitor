import unittest
from unittest.mock import patch

import requests

from monitor import product_key, updated_state_and_alert
from monitors.discovery import discover_from_html
from monitors.generic import GenericMonitor, parse_price


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.monitor = GenericMonitor(requests.Session())
        self.product = {"name": "ETB test", "store": "Test", "url": "https://example.test/p", "max_price": 65}

    def test_french_prices(self):
        self.assertEqual(parse_price("55,99 €"), 55.99)
        self.assertEqual(parse_price("55.99€"), 55.99)
        self.assertEqual(parse_price("59 €"), 59.0)

    def test_active_button_means_available(self):
        result = self.monitor.parse('<span class="price">55,99 €</span><button>Ajouter au panier</button>', 200, self.product)
        self.assertEqual(result.status, "available")
        self.assertEqual(result.price, 55.99)

    def test_disabled_button_is_not_available(self):
        result = self.monitor.parse('<button disabled>Ajouter au panier</button>', 200, self.product)
        self.assertEqual(result.status, "unavailable")

    def test_json_ld_stock(self):
        page = '<script type="application/ld+json">{"offers":{"price":"59.00","availability":"https://schema.org/InStock"}}</script>'
        result = self.monitor.parse(page, 200, self.product)
        self.assertEqual(result.status, "available")
        self.assertEqual(result.price, 59.0)

    def test_unknown_does_not_rearm_alert(self):
        previous = {"availability": "available", "alert_level": "normal", "last_alert": "date"}
        result = {"status": "unknown", "price": None, "seller": None, "direct_seller": None}
        current = updated_state_and_alert(self.product, result, previous, dry_run=True)
        self.assertEqual(current["availability"], "available")
        self.assertEqual(current["alert_level"], "normal")

    def test_key_is_stable(self):
        self.assertEqual(product_key(self.product), product_key(dict(self.product)))

    @patch("monitor.send_telegram_alert", return_value=True)
    def test_alert_once_then_rearm_after_out_of_stock(self, send):
        available = {"status": "available", "price": 59.0, "seller": None, "direct_seller": True}
        unavailable = {"status": "unavailable", "price": 59.0, "seller": None, "direct_seller": True}
        state = updated_state_and_alert(self.product, available, {}, dry_run=False)
        state = updated_state_and_alert(self.product, available, state, dry_run=False)
        self.assertEqual(send.call_count, 1)
        state = updated_state_and_alert(self.product, unavailable, state, dry_run=False)
        updated_state_and_alert(self.product, available, state, dry_run=False)
        self.assertEqual(send.call_count, 2)

    @patch("monitor.send_telegram_alert", return_value=True)
    def test_price_limit_suppresses_normal_alert(self, send):
        result = {"status": "available", "price": 99.99, "seller": None, "direct_seller": True}
        state = updated_state_and_alert(self.product, result, {}, dry_run=False)
        self.assertEqual(send.call_count, 0)
        self.assertEqual(state["alert_level"], "none")

    @patch("monitor.send_telegram_alert", return_value=True)
    def test_marketplace_requires_direct_seller(self, send):
        product = dict(self.product, require_direct_seller=True)
        result = {"status": "available", "price": 59.0, "seller": "Vendeur tiers", "direct_seller": False}
        updated_state_and_alert(product, result, {}, dry_run=False)
        self.assertEqual(send.call_count, 0)

    def test_discovery_finds_matching_product_link(self):
        source = {
            "id": "search-test",
            "name": "Recherche",
            "store": "Test",
            "url": "https://shop.test/search?q=pokemon",
            "keywords": ["Pokémon 30e anniversaire"],
            "eans": ["0196214144835"],
            "link_patterns": ["/product/"],
            "max_price": 65,
        }
        page = """
        <body>
        <div class="card"><a href="/product/etb-30">ETB Pokémon 30e anniversaire</a></div>
        <div class="card"><a href="/product/random">Booster ordinaire</a></div>
        </body>
        """
        products = discover_from_html(source, page)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["url"], "https://shop.test/product/etb-30")
        self.assertEqual(products[0]["max_price"], 65)

    def test_discovery_matches_ean_in_card_context(self):
        source = {
            "id": "ean-test",
            "name": "Recherche",
            "store": "Test",
            "url": "https://shop.test/search",
            "keywords": [],
            "eans": ["0196214144835"],
            "link_patterns": ["/p/"],
        }
        page = '<article><span>EAN 0196214144835</span><a href="/p/etb">Voir la fiche</a></article>'
        products = discover_from_html(source, page)
        self.assertEqual(len(products), 1)


if __name__ == "__main__":
    unittest.main()
