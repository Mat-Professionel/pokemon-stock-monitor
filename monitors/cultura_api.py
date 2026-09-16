"""Client minimal pour le GraphQL public chargé par les pages Cultura."""

from __future__ import annotations

import json
from typing import Any

import requests

from config import REQUEST_TIMEOUT_SECONDS, USER_AGENT
from .generic import Result, normalized, parse_price


ENDPOINT = "https://www.cultura.com/m2/graphql"
SEARCH_QUERY = """
query StockSearch($search: String, $pageSize: Int) {
  products(search: $search, pageSize: $pageSize, currentPage: 1, resolverLight: 1) {
    items {
      id sku name ean url_key
      stock_item_extra {
        front_availability availability_date order_delay
        offer { front_availability seller_code qty }
      }
      price_range {
        minimum_price { final_price { value currency } }
      }
      mp_info {
        offers { quantity price shop { name } }
      }
    }
  }
}
"""


def search(session: requests.Session, term: str, page_size: int = 20) -> list[dict[str, Any]]:
    response = session.get(
        ENDPOINT,
        params={"query": SEARCH_QUERY, "variables": json.dumps({"search": term, "pageSize": page_size})},
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "Referer": "https://www.cultura.com/",
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL Cultura: {payload['errors'][0].get('message', 'erreur')}")
    items = payload.get("data", {}).get("products", {}).get("items", [])
    return [item for item in items if isinstance(item, dict)]


def item_url(item: dict[str, Any]) -> str:
    key = str(item.get("url_key", "")).strip().strip("/")
    return f"https://www.cultura.com/p-{key}.html" if key else ""


def _quantity(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0


def result_for(product: dict[str, Any], session: requests.Session) -> Result | None:
    lookup = str(product.get("ean") or product.get("sku") or "").strip()
    if not lookup:
        return None
    items = search(session, lookup, 10)
    matching = [
        item
        for item in items
        if lookup in {str(item.get("ean", "")), str(item.get("sku", ""))}
    ]
    if not matching:
        return None
    item = matching[0]
    stock = item.get("stock_item_extra") or {}
    front = normalized(str(stock.get("front_availability", "")))
    direct_offers = stock.get("offer") or []
    marketplace_offers = (item.get("mp_info") or {}).get("offers") or []

    price = parse_price(
        (((item.get("price_range") or {}).get("minimum_price") or {}).get("final_price") or {}).get("value")
    )
    seller = "Cultura"
    direct_seller = True
    positive_qty = any(_quantity(offer.get("qty")) > 0 for offer in direct_offers if isinstance(offer, dict))

    if not positive_qty and marketplace_offers:
        offer = min(
            (value for value in marketplace_offers if isinstance(value, dict)),
            key=lambda value: parse_price(value.get("price")) or float("inf"),
            default=None,
        )
        if offer:
            market_price = parse_price(offer.get("price"))
            if market_price is not None:
                price = market_price
            seller = str((offer.get("shop") or {}).get("name") or "Vendeur partenaire")
            direct_seller = "cultura" in normalized(seller)
            positive_qty = _quantity(offer.get("quantity")) > 0

    if any(word in front for word in ("unavailable", "indisponible", "out of stock", "epuise")):
        status, reason = "unavailable", "API Cultura : indisponible"
    elif positive_qty or any(word in front for word in ("available", "disponible", "in stock", "en stock")):
        status, reason = "available", "API Cultura : stock disponible"
    else:
        status, reason = "unknown", f"API Cultura : état {front or 'non précisé'}"
    return Result(status, price, seller, direct_seller, reason, 200)
