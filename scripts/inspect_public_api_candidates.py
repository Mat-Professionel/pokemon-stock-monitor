#!/usr/bin/env python3
"""Liste les réponses JSON publiques chargées par des pages de boutique.

Cet outil de diagnostic n'affiche ni en-têtes, ni cookies, ni corps de réponse.
Il sert uniquement à repérer les endpoints candidats vus par un navigateur normal.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Response, sync_playwright

from config import USER_AGENT


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stores", nargs="*", default=[])
    args = parser.parse_args()
    products = json.loads((Path(__file__).resolve().parents[1] / "products.json").read_text())
    wanted = {value.casefold() for value in args.stores}
    selected = []
    seen_stores = set()
    for product in products:
        store = str(product.get("store", ""))
        if wanted and store.casefold() not in wanted:
            continue
        if store in seen_stores:
            continue
        selected.append(product)
        seen_stores.add(store)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for product in selected:
            print(f"\n## {product['store']} | {product['url']}")
            page = browser.new_page(user_agent=USER_AGENT, locale="fr-FR")
            candidates: dict[str, tuple[int, str, str]] = {}

            def observe(response: Response) -> None:
                content_type = response.headers.get("content-type", "").lower()
                path = urlparse(response.url).path.lower()
                looks_like_api = any(word in path for word in ("api", "graphql", "stock", "inventory", "availability", "search"))
                if "json" not in content_type and not looks_like_api:
                    return
                keys = ""
                if "json" in content_type:
                    try:
                        payload = response.json()
                        if isinstance(payload, dict):
                            keys = ",".join(list(map(str, payload.keys()))[:16])
                        elif isinstance(payload, list):
                            keys = f"list[{len(payload)}]"
                    except Exception:
                        keys = "json-invalide"
                candidates[response.url] = (response.status, content_type.split(";", 1)[0], keys)

            page.on("response", observe)
            try:
                response = page.goto(product["url"], wait_until="domcontentloaded", timeout=25_000)
                page.wait_for_timeout(4_000)
                print(f"page_status={response.status if response else 'none'} candidates={len(candidates)}")
                for url, details in list(candidates.items())[:40]:
                    status, content_type, keys = details
                    print(f"{status} {content_type} keys=[{keys}] {url}")
            except Exception as exc:
                print(f"navigation_error={type(exc).__name__}: {exc}")
            finally:
                page.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
