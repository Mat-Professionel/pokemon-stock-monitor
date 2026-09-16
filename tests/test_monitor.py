import unittest
from unittest.mock import patch

import requests

from monitor import product_key, updated_state_and_alert
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


if __name__ == "__main__":
    unittest.main()
