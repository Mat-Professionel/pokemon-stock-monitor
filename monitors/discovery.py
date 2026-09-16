"""Découverte prudente de fiches produit depuis une recherche ou une catégorie."""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import REQUEST_TIMEOUT_SECONDS, USER_AGENT
from .generic import GenericMonitor, normalized


def _matches(text: str, url: str, source: dict[str, Any]) -> bool:
    haystack = normalized(f"{text} {url}")
    eans = [str(value).strip() for value in source.get("eans", []) if str(value).strip()]
    keywords = [normalized(str(value)) for value in source.get("keywords", []) if str(value).strip()]
    exclusions = [normalized(str(value)) for value in source.get("exclude_keywords", []) if str(value).strip()]
    required = [normalized(str(value)) for value in source.get("required_terms", []) if str(value).strip()]
    if any(word in haystack for word in exclusions):
        return False
    matched = any(ean in haystack for ean in eans) or any(keyword in haystack for keyword in keywords)
    return matched and (not required or any(word in haystack for word in required))


def _valid_product_link(url: str, source: dict[str, Any]) -> bool:
    patterns = source.get("link_patterns", [])
    exclusions = source.get("exclude_link_patterns", [])
    if any(pattern in url for pattern in exclusions):
        return False
    return not patterns or any(pattern in url for pattern in patterns)


def discover_from_html(source: dict[str, Any], page_html: str) -> list[dict[str, Any]]:
    """Extrait les liens de produits pertinents sans effectuer de requête réseau."""
    soup = BeautifulSoup(page_html, "html.parser")
    base_url = source["url"]
    base_host = urlparse(base_url).netloc.removeprefix("www.")
    found: dict[str, dict[str, Any]] = {}
    limit = int(source.get("max_discovered_products", 12))

    for anchor in soup.find_all("a", href=True):
        url = urljoin(base_url, anchor["href"].strip()).split("#", 1)[0]
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or parsed.netloc.removeprefix("www.") != base_host:
            continue
        if url.rstrip("/") == base_url.rstrip("/") or not _valid_product_link(url, source):
            continue
        context = anchor.get_text(" ", strip=True)
        parent = anchor.parent
        # Remonte jusqu'à la carte produit, jamais jusqu'au document complet :
        # les boutiques imbriquent souvent le titre à 2 ou 3 niveaux du lien.
        for _ in range(4):
            if parent is None or parent.name in ("body", "html"):
                break
            candidate = parent.get_text(" ", strip=True)
            if len(candidate) <= 700:
                context = f"{context} {candidate}"
            parent = parent.parent
        if not _matches(context, url, source):
            continue
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title or len(title) < 5:
            title = " ".join(context.split())[:180]
        key = hashlib.sha256(url.encode()).hexdigest()[:12]
        found[url] = {
            "id": f"{source.get('id', 'discovery')}:{key}",
            "name": title,
            "store": source["store"],
            "url": url,
            "max_price": source.get("max_price"),
            "require_direct_seller": source.get("require_direct_seller", False),
            "alert_if_too_expensive": source.get("alert_if_too_expensive", False),
            "engine": source.get("product_engine", "requests"),
            "type": "product",
            "discovered_from": source.get("id"),
        }
        if len(found) >= limit:
            break
    return list(found.values())


def discover_products(source: dict[str, Any], session: requests.Session) -> list[dict[str, Any]]:
    """Charge une page de veille puis retourne les nouvelles fiches candidates."""
    try:
        if source.get("engine") == "playwright":
            page_html, status = GenericMonitor(session, "playwright")._fetch_playwright(source["url"])
            if status >= 400:
                raise RuntimeError(f"HTTP {status}")
        else:
            response = session.get(
                source["url"],
                timeout=REQUEST_TIMEOUT_SECONDS,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"},
            )
            response.raise_for_status()
            page_html = response.text
        return discover_from_html(source, page_html)
    except Exception as exc:
        logging.warning("[%s] découverte impossible: %s", source["store"].upper(), exc)
        return []
