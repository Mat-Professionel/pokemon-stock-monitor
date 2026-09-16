"""Moteur HTTP/HTML partagé et extensible."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup

from config import REQUEST_TIMEOUT_SECONDS, USER_AGENT


PRICE_RE = re.compile(r"(?<!\d)(\d{1,4}(?:[\s\u00a0]\d{3})*(?:[,.]\d{1,2})?)\s*€", re.I)


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).strip().lower()


def parse_price(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = PRICE_RE.search(value)
    raw = match.group(1) if match else value.strip()
    raw = raw.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        price = float(raw)
        return price if 0 < price < 100_000 else None
    except ValueError:
        return None


@dataclass
class Result:
    status: str = "unknown"  # available, unavailable, unknown
    price: float | None = None
    seller: str | None = None
    direct_seller: bool | None = None
    reason: str = "Aucun signal fiable"
    http_status: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GenericMonitor:
    positive_signals = (
        "ajouter au panier",
        "acheter maintenant",
        "commander",
        "en stock",
        "disponible en ligne",
    )
    negative_signals = (
        "rupture de stock",
        "indisponible",
        "epuise",
        "bientot disponible",
        "precommande terminee",
        "produit indisponible",
        "temporairement indisponible",
    )
    direct_seller_aliases: tuple[str, ...] = ()
    marketplace = False
    price_selectors = (
        '[itemprop="price"]',
        'meta[property="product:price:amount"]',
        '[data-price]',
        '.price',
        '.product-price',
    )

    def __init__(self, session: requests.Session, engine: str = "requests") -> None:
        self.session = session
        self.engine = engine

    def check(self, product: dict[str, Any]) -> Result:
        try:
            if self.engine == "playwright":
                html, status_code = self._fetch_playwright(product["url"])
                if status_code in (403, 429) or status_code >= 500:
                    return Result(reason=f"HTTP {status_code}", http_status=status_code, error=f"HTTP {status_code}")
            else:
                response = self.session.get(
                    product["url"],
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"},
                )
                status_code = response.status_code
                if status_code in (403, 429) or status_code >= 500:
                    return Result(reason=f"HTTP {status_code}", http_status=status_code, error=f"HTTP {status_code}")
                response.raise_for_status()
                html = response.text
            return self.parse(html, status_code, product)
        except Exception as exc:  # chaque produit reste isolé
            logging.debug("Échec complet", exc_info=True)
            return Result(reason=str(exc), error=f"{type(exc).__name__}: {exc}")

    def _fetch_playwright(self, url: str) -> tuple[str, int]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright n'est pas installé") from exc
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT, locale="fr-FR")
            response = page.goto(url, wait_until="domcontentloaded", timeout=REQUEST_TIMEOUT_SECONDS * 1000)
            page.wait_for_timeout(1500)
            html = page.content()
            code = response.status if response else 200
            browser.close()
            return html, code

    def parse(self, html: str, status_code: int, product: dict[str, Any]) -> Result:
        soup = BeautifulSoup(html, "html.parser")
        text = normalized(soup.get_text(" ", strip=True))
        structured = self._structured_data(soup)
        price = self._extract_price(soup, structured, text)
        schema_status = self._schema_availability(structured)
        seller = self._extract_seller(soup, structured, text)
        direct = self._is_direct_seller(seller)

        if schema_status == "unavailable":
            status, reason = "unavailable", "Données produit : indisponible"
        elif any(signal in text for signal in map(normalized, self.negative_signals)):
            status, reason = "unavailable", "Signal d'indisponibilité détecté"
        elif self._has_disabled_buy_button(soup):
            status, reason = "unavailable", "Bouton d'achat désactivé"
        elif self._has_enabled_buy_button(soup):
            status, reason = "available", "Bouton d'achat actif"
        elif schema_status == "available":
            status, reason = "available", "Données produit : en stock"
        elif any(signal in text for signal in map(normalized, self.positive_signals)):
            status, reason = "available", "Signal de disponibilité détecté"
        else:
            status, reason = "unknown", "Aucun signal d'achat fiable"

        return Result(status, price, seller, direct, reason, status_code)

    def _has_enabled_buy_button(self, soup: BeautifulSoup) -> bool:
        for element in soup.find_all(["button", "a", "input"]):
            label = normalized(" ".join(filter(None, [element.get_text(" ", strip=True), element.get("value"), element.get("aria-label")])))
            if not any(signal in label for signal in ("ajouter au panier", "acheter", "commander")):
                continue
            classes = " ".join(element.get("class", []))
            disabled = element.has_attr("disabled") or element.get("aria-disabled") == "true" or "disabled" in classes.lower()
            if not disabled:
                return True
        return False

    def _has_disabled_buy_button(self, soup: BeautifulSoup) -> bool:
        for element in soup.find_all(["button", "a", "input"]):
            label = normalized(" ".join(filter(None, [element.get_text(" ", strip=True), element.get("value"), element.get("aria-label")])))
            if not any(signal in label for signal in ("ajouter au panier", "acheter", "commander")):
                continue
            classes = " ".join(element.get("class", []))
            if element.has_attr("disabled") or element.get("aria-disabled") == "true" or "disabled" in classes.lower():
                return True
        return False

    def _structured_data(self, soup: BeautifulSoup) -> list[Any]:
        values: list[Any] = []
        for script in soup.select('script[type="application/ld+json"], script[type="application/json"]'):
            try:
                values.append(json.loads(script.string or script.get_text()))
            except (json.JSONDecodeError, TypeError):
                continue
        return values

    def _walk(self, value: Any):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from self._walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._walk(child)

    def _schema_availability(self, values: list[Any]) -> str | None:
        for value in values:
            for node in self._walk(value):
                availability = normalized(str(node.get("availability", "")))
                if any(word in availability for word in ("instock", "limitedavailability", "preorder")):
                    return "available"
                if any(word in availability for word in ("outofstock", "soldout", "discontinued")):
                    return "unavailable"
                for key in ("inventoryLevel", "inventoryQuantity", "stock"):
                    stock = node.get(key)
                    if isinstance(stock, dict):
                        stock = stock.get("value")
                    try:
                        if stock is not None:
                            return "available" if float(stock) > 0 else "unavailable"
                    except (TypeError, ValueError):
                        pass
        return None

    def _extract_price(self, soup: BeautifulSoup, values: list[Any], text: str) -> float | None:
        for value in values:
            for node in self._walk(value):
                for key in ("price", "lowPrice"):
                    price = parse_price(node.get(key))
                    if price is not None:
                        return price
        for selector in self.price_selectors:
            for element in soup.select(selector):
                for candidate in (element.get("content"), element.get("data-price"), element.get_text(" ", strip=True)):
                    price = parse_price(candidate)
                    if price is not None:
                        return price
        match = PRICE_RE.search(text)
        return parse_price(match.group(0)) if match else None

    def _extract_seller(self, soup: BeautifulSoup, values: list[Any], text: str) -> str | None:
        for value in values:
            for node in self._walk(value):
                seller = node.get("seller")
                if isinstance(seller, dict) and seller.get("name"):
                    return str(seller["name"]).strip()
                if isinstance(seller, str):
                    return seller.strip()
        match = re.search(r"(?:vendu|expedie) par\s+([^|•,]{2,60})", text, re.I)
        return match.group(1).strip() if match else None

    def _is_direct_seller(self, seller: str | None) -> bool | None:
        if not self.marketplace:
            return True
        if not seller:
            return None
        candidate = normalized(seller)
        return any(normalized(alias) in candidate for alias in self.direct_seller_aliases)
