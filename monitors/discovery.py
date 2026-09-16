"""Découverte prudente de fiches produit depuis une recherche ou une catégorie."""

from __future__ import annotations

import hashlib
import gzip
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import unquote, urljoin, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from config import REQUEST_TIMEOUT_SECONDS, USER_AGENT
from .generic import GenericMonitor, normalized


def _searchable(value: str) -> str:
    """Normalise aussi les séparateurs d'URL (tirets, slash, %xx)."""
    return re.sub(r"[^a-z0-9]+", " ", normalized(unquote(value))).strip()


def _matches(text: str, url: str, source: dict[str, Any]) -> bool:
    haystack = _searchable(f"{text} {url}")
    eans = [str(value).strip() for value in source.get("eans", []) if str(value).strip()]
    keywords = [_searchable(str(value)) for value in source.get("keywords", []) if str(value).strip()]
    exclusions = [_searchable(str(value)) for value in source.get("exclude_keywords", []) if str(value).strip()]
    required = [_searchable(str(value)) for value in source.get("required_terms", []) if str(value).strip()]
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


def _product_from_url(source: dict[str, Any], url: str, title: str = "") -> dict[str, Any]:
    key = hashlib.sha256(url.encode()).hexdigest()[:12]
    if not title:
        slug = unquote(urlparse(url).path.rstrip("/").split("/")[-1])
        title = re.sub(r"[-_]+", " ", slug.rsplit(".", 1)[0]).strip()
    return {
        "id": f"{source.get('id', 'discovery')}:{key}",
        "name": " ".join(title.split())[:180] or source["name"],
        "store": source["store"],
        "url": url,
        "max_price": source.get("max_price"),
        "require_direct_seller": source.get("require_direct_seller", False),
        "alert_if_too_expensive": source.get("alert_if_too_expensive", False),
        "engine": source.get("product_engine", "requests"),
        "type": "product",
        "discovered_from": source.get("id"),
    }


def _xml_root(content: bytes) -> ElementTree.Element:
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    return ElementTree.fromstring(content)


def _tag_name(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def _fetch_xml(session: requests.Session, url: str) -> ElementTree.Element:
    response = session.get(
        url,
        timeout=min(REQUEST_TIMEOUT_SECONDS, 15),
        headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"},
    )
    response.raise_for_status()
    return _xml_root(response.content)


def discover_from_sitemap(source: dict[str, Any], session: requests.Session) -> list[dict[str, Any]]:
    """Cherche EAN/mots-clés dans un sitemap ou un index de sitemaps public."""
    root = _fetch_xml(session, source["url"])
    roots: list[ElementTree.Element] = []
    if _tag_name(root) == "sitemapindex":
        locations = [
            (node.text or "").strip()
            for node in root.iter()
            if _tag_name(node) == "loc" and (node.text or "").strip()
        ]
        patterns = [str(value).lower() for value in source.get("sitemap_include_patterns", [])]
        if patterns:
            locations = [url for url in locations if any(pattern in url.lower() for pattern in patterns)]
        limit = int(source.get("max_sitemap_files", 50))
        if source.get("sitemap_order", "last") == "last":
            locations = locations[-limit:]
        else:
            locations = locations[:limit]
        with ThreadPoolExecutor(max_workers=min(4, len(locations) or 1)) as executor:
            futures = {executor.submit(_fetch_xml, session, url): url for url in locations}
            for future in as_completed(futures):
                try:
                    roots.append(future.result())
                except Exception as exc:
                    logging.warning("[%s] sous-sitemap inaccessible (%s): %s", source["store"].upper(), futures[future], exc)
    else:
        roots.append(root)

    found: dict[str, dict[str, Any]] = {}
    limit = int(source.get("max_discovered_products", 20))
    for sitemap_root in roots:
        for entry in sitemap_root:
            if _tag_name(entry) != "url":
                continue
            url = ""
            title = ""
            context: list[str] = []
            for node in entry.iter():
                value = (node.text or "").strip()
                if not value:
                    continue
                context.append(value)
                tag = _tag_name(node)
                if tag == "loc" and not url:
                    url = value
                elif tag == "title" and not title:
                    title = value
            if not url or not _valid_product_link(url, source):
                continue
            if _matches(" ".join(context), url, source):
                found[url] = _product_from_url(source, url, title)
                if len(found) >= limit:
                    return list(found.values())
    return list(found.values())


def discover_products(source: dict[str, Any], session: requests.Session) -> list[dict[str, Any]]:
    """Charge une page de veille puis retourne les nouvelles fiches candidates."""
    if source.get("type") == "sitemap":
        return discover_from_sitemap(source, session)
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
