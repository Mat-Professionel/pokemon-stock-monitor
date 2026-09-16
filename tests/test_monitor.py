import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import requests

from monitor import (
    expand_discovery_sources,
    product_key,
    send_health_report,
    update_store_health,
    updated_state_and_alert,
)
from monitors.discovery import discover_from_html, discover_from_sitemap
from monitors.generic import GenericMonitor, parse_price
from monitors.cultura_api import result_for as cultura_result_for


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

    @patch("monitor.send_telegram_message", return_value=True)
    def test_store_health_alerts_on_fifth_failure_then_recovers(self, send):
        states = {}
        for _ in range(4):
            update_store_health("Fnac", "HTTP 403", states, dry_run=False, fallback="Playwright actif")
        send.assert_not_called()

        update_store_health("Fnac", "HTTP 403", states, dry_run=False, fallback="Playwright actif")
        self.assertEqual(send.call_count, 1)
        self.assertTrue(states["fnac"]["alerted"])
        self.assertEqual(states["fnac"]["consecutive_errors"], 5)

        update_store_health("Fnac", None, states, dry_run=False)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(states["fnac"]["consecutive_errors"], 0)
        self.assertFalse(states["fnac"]["alerted"])

    @patch("monitor.send_telegram_message", return_value=True)
    @patch("monitor.test_telegram_connection", return_value=True)
    def test_health_report_flags_a_stale_scan(self, telegram_test, send):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            products = root / "products.json"
            state = root / "state.json"
            products.write_text("[]", encoding="utf-8")
            state.write_text(json.dumps({"last_scan": "2020-01-01T00:00:00+01:00"}), encoding="utf-8")
            with (
                patch("monitor.config.PRODUCTS_FILE", products),
                patch("monitor.config.DISCOVERED_PRODUCTS_FILE", None),
                patch("monitor.config.HEALTH_STATE_FILES", [state]),
            ):
                self.assertTrue(send_health_report())
        self.assertIn("Monitor en retard", send.call_args.args[0])
        self.assertIn("moins de 3 minutes", send.call_args.args[0])
        self.assertEqual(
            send.call_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
            "restart_all",
        )

    @patch("monitor.send_telegram_message", return_value=True)
    @patch("monitor.test_telegram_connection", return_value=True)
    def test_confidence_report_counts_recent_functional_routes(self, telegram_test, send):
        now = datetime.now(ZoneInfo("Europe/Paris")).isoformat()
        configured = [
            {"id": "p1", "name": "Produit", "store": "Boutique A", "url": "https://a.test/p", "type": "product"},
            {"id": "s1", "name": "Recherche", "store": "Boutique A", "url": "https://a.test/s", "type": "search"},
            {"id": "s2", "name": "Recherche", "store": "Boutique B", "url": "https://b.test/s", "type": "search"},
        ]
        routes = {
            "product:p1": {"functional": True, "last_check": now},
            "discovery:s1": {"functional": False, "last_check": now},
            "discovery:s2": {"functional": False, "last_check": now},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            products = root / "products.json"
            state = root / "state.json"
            products.write_text(json.dumps(configured), encoding="utf-8")
            state.write_text(json.dumps({"last_scan": now, "route_health": routes}), encoding="utf-8")
            with (
                patch("monitor.config.PRODUCTS_FILE", products),
                patch("monitor.config.DISCOVERED_PRODUCTS_FILE", None),
                patch("monitor.config.HEALTH_STATE_FILES", [state]),
            ):
                self.assertTrue(send_health_report())
        message = send.call_args.args[0]
        self.assertIn("Boutiques vérifiables : 1/2", message)
        self.assertIn("Voies fonctionnelles : 1/3 (33 %)", message)
        self.assertIn("Boutiques aveugles : Boutique B", message)
        self.assertIn("Dernier test d'alerte : ✅", message)

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

    def test_sitemap_discovers_hyphenated_keyword_url(self):
        source = {
            "id": "sitemap-test",
            "name": "Sitemap",
            "store": "Test",
            "url": "https://shop.test/sitemap.xml",
            "keywords": ["Pokémon 30e anniversaire"],
            "link_patterns": ["/pokemon/"],
            "max_price": 65,
            "type": "sitemap",
        }
        sitemap = b'''<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://shop.test/pokemon/coffret-pokemon-30e-anniversaire.html</loc></url>
          <url><loc>https://shop.test/pokemon/booster-standard.html</loc></url>
        </urlset>'''
        response = unittest.mock.Mock(content=sitemap)
        response.raise_for_status.return_value = None
        session = unittest.mock.Mock()
        session.get.return_value = response
        products = discover_from_sitemap(source, session)
        self.assertEqual(len(products), 1)
        self.assertIn("30e-anniversaire", products[0]["url"])

    @patch("monitor.check_discovery_source")
    def test_explicit_product_wins_over_discovered_duplicate(self, check_source):
        explicit = {
            "id": "explicit",
            "name": "Produit exact",
            "store": "Test",
            "url": "https://shop.test/p/1",
            "max_price": 30,
            "type": "product",
        }
        source = {
            "id": "source",
            "name": "Recherche",
            "store": "Test",
            "url": "https://shop.test/search",
            "type": "search",
        }
        check_source.return_value = (source, [dict(explicit, id="found", max_price=65)])
        products = expand_discovery_sources([explicit, source], {"products": {}}, test_mode=True)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["id"], "explicit")
        self.assertEqual(products[0]["max_price"], 30)

    @patch("monitor.check_discovery_source")
    def test_recent_discovery_error_uses_cached_products(self, check_source):
        source = {
            "id": "blocked-source",
            "name": "Recherche",
            "store": "Test",
            "url": "https://shop.test/search",
            "type": "search",
        }
        cached_product = {
            "id": "cached",
            "name": "Produit en cache",
            "store": "Test",
            "url": "https://shop.test/p/1",
            "type": "product",
        }
        state = {
            "products": {},
            "discovery": {
                "blocked-source": {
                    "last_attempt": datetime.now(ZoneInfo("Europe/Paris")).isoformat(),
                    "last_error": "HTTP 403",
                    "products": [cached_product],
                }
            },
        }
        products = expand_discovery_sources([source], state)
        check_source.assert_not_called()
        self.assertEqual(products, [cached_product])

    @patch("monitors.cultura_api.search")
    def test_cultura_api_returns_price_and_unavailable(self, search):
        search.return_value = [
            {
                "sku": "13360309",
                "ean": "4521329462424",
                "stock_item_extra": {"front_availability": "unavailable", "offer": []},
                "price_range": {"minimum_price": {"final_price": {"value": 19.99, "currency": "EUR"}}},
                "mp_info": {"offers": []},
            }
        ]
        product = {
            "name": "Produit Cultura",
            "store": "Cultura",
            "url": "https://www.cultura.com/p-test.html",
            "ean": "4521329462424",
        }
        result = cultura_result_for(product, requests.Session())
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.price, 19.99)
        self.assertEqual(result.seller, "Cultura")


if __name__ == "__main__":
    unittest.main()
