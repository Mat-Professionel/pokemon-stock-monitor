#!/usr/bin/env python3
"""Surveille les produits configurés et alerte lors d'un passage en stock."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config
from monitors import monitor_for
from monitors.discovery import discover_products


def paris_now() -> datetime:
    return datetime.now(ZoneInfo(config.TIMEZONE))


def now_iso() -> str:
    return paris_now().isoformat(timespec="seconds")


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=config.REQUEST_RETRIES,
        backoff_factor=config.REQUEST_BACKOFF_SECONDS,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def load_json(path: Path, default: Any) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON invalide dans {path}: {exc}") from exc


def save_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def product_key(product: dict[str, Any]) -> str:
    if product.get("id"):
        return str(product["id"])
    raw = "|".join(str(product.get(key, "")) for key in ("store", "name", "url"))
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def validate_product(product: Any, index: int) -> str | None:
    if not isinstance(product, dict):
        return f"Entrée {index}: doit être un objet JSON"
    for field in ("name", "store", "url"):
        if not product.get(field):
            return f"Entrée {index}: champ '{field}' manquant"
    url = str(product["url"])
    if "PASTE_" in url or url.startswith("URL_"):
        return "placeholder"
    if urlparse(url).scheme not in ("http", "https"):
        return f"Entrée {index}: URL invalide"
    try:
        if product.get("max_price") is not None:
            float(product["max_price"])
    except (TypeError, ValueError):
        return f"Entrée {index}: max_price doit être un nombre"
    return None


def check_product(product: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    session = make_session()
    try:
        result = monitor_for(product, session).check(product)
        return product, result.to_dict()
    finally:
        session.close()


def check_discovery_source(source: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    session = make_session()
    try:
        return source, discover_products(source, session)
    finally:
        session.close()


def expand_discovery_sources(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    direct = [item for item in entries if item.get("type", "product") == "product"]
    sources = [item for item in entries if item.get("type") in ("search", "category_search", "discovery")]
    if not sources:
        return direct

    discovered: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(config.MAX_WORKERS, len(sources))) as executor:
        futures = {executor.submit(check_discovery_source, source): source for source in sources}
        for future in as_completed(futures):
            source = futures[future]
            try:
                _, products = future.result()
            except Exception as exc:
                logging.warning("[%s] découverte en erreur: %s", source["store"].upper(), exc)
                continue
            logging.info("[%s] découverte → %d fiche(s) correspondante(s)", source["store"].upper(), len(products))
            discovered.extend(products)

    unique: dict[str, dict[str, Any]] = {}
    for product in [*direct, *discovered]:
        unique[product["url"]] = product
    return list(unique.values())


def money(price: float | None) -> str:
    return "non détecté" if price is None else f"{price:.2f} €".replace(".", ",")


def send_telegram_message(message: str) -> bool:
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        logging.error("Secrets TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID absents")
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        payload = response.json()
        if not response.ok:
            logging.error(
                "Erreur Telegram HTTP %s: %s",
                response.status_code,
                payload.get("description", "réponse refusée"),
            )
            return False
        if not payload.get("ok"):
            raise RuntimeError(payload.get("description", "Erreur Telegram inconnue"))
        return True
    except requests.RequestException as exc:
        # Ne jamais journaliser l'URL complète : elle contient le token du bot.
        logging.error("Erreur réseau Telegram: %s", type(exc).__name__)
        return False
    except (ValueError, RuntimeError) as exc:
        logging.error("Réponse Telegram invalide: %s", exc)
        return False


def send_telegram_alert(product: dict[str, Any], result: dict[str, Any], expensive: bool = False) -> bool:
    title = "⚠️ PRODUIT DISPONIBLE MAIS TROP CHER" if expensive else "🚨 DROP POKÉMON 30 ANS"
    price_line = money(result.get("price"))
    lines = [
        f"<b>{title}</b>",
        "",
        f"🏪 {html.escape(str(product['store']))}",
        f"📦 {html.escape(str(product['name']))}",
        f"💰 {html.escape(price_line)}",
        "✅ DISPONIBLE",
    ]
    if expensive:
        lines.append(f"💸 Limite : {money(float(product['max_price']))}")
    if result.get("seller"):
        lines.append(f"👤 Vendeur : {html.escape(str(result['seller']))}")
    lines.extend(
        [
            "",
            f'🔗 <a href="{html.escape(str(product["url"]), quote=True)}">Acheter</a>',
            "",
            f"⏰ Détecté à : {paris_now().strftime('%d/%m/%Y à %H:%M:%S')} (Paris)",
        ]
    )
    return send_telegram_message("\n".join(lines))


def log_result(product: dict[str, Any], result: dict[str, Any]) -> None:
    tag = str(product["store"]).upper()
    name = product["name"]
    if result.get("error"):
        logging.warning("[%s] %s → erreur: %s", tag, name, result["reason"])
    elif result["status"] == "available":
        logging.info("[%s] %s → DISPONIBLE → %s", tag, name, money(result.get("price")))
    elif result["status"] == "unavailable":
        logging.info("[%s] %s → indisponible", tag, name)
    else:
        logging.warning("[%s] %s → état inconnu (%s)", tag, name, result["reason"])


def updated_state_and_alert(
    product: dict[str, Any], result: dict[str, Any], previous: dict[str, Any], dry_run: bool
) -> dict[str, Any]:
    current = dict(previous)
    current.update({"name": product["name"], "store": product["store"], "url": product["url"]})
    status = result["status"]

    # Une erreur transitoire ne transforme pas un produit en rupture et ne réarme pas l'alerte.
    if status == "unknown":
        return current

    price = result.get("price")
    max_price = float(product["max_price"]) if product.get("max_price") is not None else None
    expensive = status == "available" and price is not None and max_price is not None and price > max_price
    direct_required = bool(product.get("require_direct_seller", False))
    seller_allowed = not direct_required or result.get("direct_seller") is True
    desired_alert = "none"
    if status == "available" and seller_allowed:
        desired_alert = "expensive" if expensive else "normal"

    previous_alert = previous.get("alert_level", "none")
    should_alert = desired_alert == "normal" and previous_alert != "normal"
    should_alert_expensive = (
        desired_alert == "expensive"
        and previous_alert not in ("expensive", "normal")
        and bool(product.get("alert_if_too_expensive", config.ALERT_EXPENSIVE_PRODUCTS))
    )

    alert_sent = False
    if not dry_run and (should_alert or should_alert_expensive):
        alert_sent = send_telegram_alert(product, result, expensive=should_alert_expensive)

    changed = (
        previous.get("availability") != status
        or previous.get("last_price") != price
        or previous.get("seller") != result.get("seller")
        or (alert_sent and previous_alert != desired_alert)
    )
    current.update(
        {
            "availability": status,
            "last_price": price,
            "seller": result.get("seller"),
            "direct_seller": result.get("direct_seller"),
            "alert_level": "none" if status == "unavailable" else previous_alert,
        }
    )
    if alert_sent:
        current["alert_level"] = desired_alert
        current["last_alert"] = now_iso()
    if changed:
        current["last_change"] = now_iso()
    return current


def run(test_mode: bool = False) -> int:
    products = load_json(config.PRODUCTS_FILE, [])
    if not isinstance(products, list):
        logging.error("products.json doit contenir une liste JSON")
        return 2

    active: list[dict[str, Any]] = []
    for index, product in enumerate(products, start=1):
        if isinstance(product, dict) and product.get("enabled", True) is False:
            continue
        error = validate_product(product, index)
        if error == "placeholder":
            logging.info("Entrée %d ignorée: remplacez son URL placeholder", index)
        elif error:
            logging.error(error)
        else:
            active.append(product)

    active = expand_discovery_sources(active)
    if not active:
        logging.warning("Aucun produit actif avec une URL réelle dans products.json")
        return 0

    state = load_json(config.STATE_FILE, {"version": 1, "products": {}})
    old_state = json.dumps(state, sort_keys=True)
    product_states = state.setdefault("products", {})

    with ThreadPoolExecutor(max_workers=min(config.MAX_WORKERS, len(active))) as executor:
        futures = {executor.submit(check_product, product): product for product in active}
        for future in as_completed(futures):
            product = futures[future]
            try:
                _, result = future.result()
            except Exception as exc:
                logging.exception("[%s] %s → échec isolé: %s", product["store"], product["name"], exc)
                continue
            log_result(product, result)
            key = product_key(product)
            product_states[key] = updated_state_and_alert(
                product, result, product_states.get(key, {}), test_mode
            )

    if test_mode:
        logging.info("Mode test: aucune alerte envoyée, state.json non modifié")
    elif json.dumps(state, sort_keys=True) != old_state:
        save_json_atomic(config.STATE_FILE, state)
        logging.info("state.json mis à jour")
    else:
        logging.info("Aucun changement d'état")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Moniteur Pokémon 30e anniversaire")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--once", action="store_true", help="effectue un scan normal")
    modes.add_argument("--test", action="store_true", help="analyse sans alerte ni écriture")
    modes.add_argument("--test-telegram", action="store_true", help="teste uniquement Telegram")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.test_telegram:
        return 0 if send_telegram_message("✅ Bot Pokémon opérationnel.") else 1
    return run(test_mode=args.test)


if __name__ == "__main__":
    sys.exit(main())
