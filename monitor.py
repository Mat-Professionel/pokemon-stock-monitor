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
from datetime import datetime, timedelta
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
from local_control import RESTART_CALLBACK_DATA, restart_local_services


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


def route_key(item: dict[str, Any]) -> str:
    kind = "product" if item.get("type", "product") == "product" else "discovery"
    identifier = str(item.get("id") or product_key(item))
    return f"{kind}:{identifier}"


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


def _cached_discovery(source: dict[str, Any], state: dict[str, Any], test_mode: bool) -> list[dict[str, Any]] | None:
    if test_mode:
        return None
    cached = state.setdefault("discovery", {}).get(str(source.get("id", source["url"])))
    if not isinstance(cached, dict):
        return None
    try:
        products = cached["products"] if isinstance(cached.get("products"), list) else []
        if cached.get("last_error") and cached.get("last_attempt"):
            last_attempt = datetime.fromisoformat(str(cached["last_attempt"]))
            retry_interval = timedelta(
                minutes=float(
                    source.get("retry_interval_minutes", config.DISCOVERY_RETRY_INTERVAL_MINUTES)
                )
            )
            if paris_now() - last_attempt < retry_interval:
                return products
        if source.get("discovery_interval_minutes") and cached.get("last_success"):
            last_success = datetime.fromisoformat(str(cached["last_success"]))
            interval = timedelta(minutes=float(source["discovery_interval_minutes"]))
            if paris_now() - last_success < interval:
                return products
    except (TypeError, ValueError):
        pass
    return None


def expand_discovery_sources(
    entries: list[dict[str, Any]], state: dict[str, Any], test_mode: bool = False
) -> list[dict[str, Any]]:
    direct = [item for item in entries if item.get("type", "product") == "product"]
    sources = [item for item in entries if item.get("type") in ("search", "category_search", "discovery", "sitemap")]
    if not sources:
        return direct

    discovered: list[dict[str, Any]] = []
    outcomes: dict[str, list[str | None]] = {}
    route_states = state.setdefault("route_health", {})
    due: list[dict[str, Any]] = []
    for source in sources:
        cached = _cached_discovery(source, state, test_mode)
        if cached is None:
            due.append(source)
        else:
            logging.info("[%s] découverte réutilisée depuis le cache → %d fiche(s)", source["store"].upper(), len(cached))
            discovered.extend(cached)

    with ThreadPoolExecutor(max_workers=min(config.MAX_WORKERS, len(due) or 1)) as executor:
        futures = {executor.submit(check_discovery_source, source): source for source in due}
        for future in as_completed(futures):
            source = futures[future]
            try:
                _, products = future.result()
            except Exception as exc:
                logging.warning("[%s] découverte en erreur: %s", source["store"].upper(), exc)
                update_route_health(
                    source,
                    False,
                    f"{type(exc).__name__}: {exc}"[:300],
                    route_states,
                )
                outcomes.setdefault(str(source["store"]), []).append(f"{type(exc).__name__}: {exc}"[:300])
                if not test_mode:
                    cache_key = str(source.get("id", source["url"]))
                    previous = state.setdefault("discovery", {}).get(cache_key, {})
                    state["discovery"][cache_key] = {
                        **previous,
                        "last_attempt": now_iso(),
                        "last_error": f"{type(exc).__name__}: {exc}"[:300],
                        "products": previous.get("products", []),
                    }
                    # Rend la mesure de confiance visible sans attendre que les
                    # autres boutiques (ou un gros sitemap) aient terminé.
                    save_json_atomic(config.STATE_FILE, state)
                continue
            logging.info("[%s] découverte → %d fiche(s) correspondante(s)", source["store"].upper(), len(products))
            update_route_health(
                source,
                True,
                f"Page analysée, {len(products)} fiche(s) correspondante(s)",
                route_states,
            )
            outcomes.setdefault(str(source["store"]), []).append(None)
            discovered.extend(products)
            if source.get("discovery_interval_minutes") and not test_mode:
                cache_key = str(source.get("id", source["url"]))
                state.setdefault("discovery", {})[cache_key] = {
                    "last_attempt": now_iso(),
                    "last_success": now_iso(),
                    "products": products,
                }
            elif not test_mode:
                state.setdefault("discovery", {}).pop(str(source.get("id", source["url"])), None)
            if not test_mode:
                save_json_atomic(config.STATE_FILE, state)

    store_states = state.setdefault("store_health", {})
    for store, values in outcomes.items():
        failures = [value for value in values if value]
        error = failures[0] if failures and len(failures) == len(values) else None
        fallback = "Playwright actif" if any(
            source.get("engine") == "playwright" and str(source.get("store")) == store for source in due
        ) else "Fallback HTML disponible"
        update_store_health(store, error, store_states, test_mode, fallback)

    unique: dict[str, dict[str, Any]] = {}
    for product in [*direct, *discovered]:
        # Une fiche explicitement configurée garde ses réglages (seuil, moteur,
        # vendeur) si elle est aussi retrouvée par une source de découverte.
        unique.setdefault(product["url"], product)
    return list(unique.values())


def money(price: float | None) -> str:
    return "non détecté" if price is None else f"{price:.2f} €".replace(".", ",")


def send_telegram_message(message: str, reply_markup: dict[str, Any] | None = None) -> bool:
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        logging.error("Secrets TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID absents")
        return False
    try:
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
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


def test_telegram_connection() -> bool:
    """Vérifie silencieusement le token et l'accès au chat du rapport."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getChat",
            json={"chat_id": config.TELEGRAM_CHAT_ID},
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        payload = response.json()
        return response.ok and payload.get("ok") is True
    except (requests.RequestException, ValueError):
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


def update_store_health(
    store: str,
    error: str | None,
    store_states: dict[str, Any],
    dry_run: bool,
    fallback: str = "HTML/Playwright disponible",
) -> None:
    key = store.strip().lower()
    previous = store_states.get(key, {})
    current = dict(previous)
    current.update({"store": store, "last_check": now_iso()})
    if error:
        failures = int(previous.get("consecutive_errors", 0)) + 1
        current.update(
            {
                "consecutive_errors": failures,
                "last_error": error[:300],
                "error_since": previous.get("error_since") or now_iso(),
                "fallback": fallback,
            }
        )
        if failures >= 5 and not previous.get("alerted") and not dry_run:
            message = "\n".join(
                [
                    f"<b>⚠️ {html.escape(store.upper())} monitor en erreur</b>",
                    "",
                    f"Échecs consécutifs : {failures}",
                    f"Depuis : {html.escape(str(current['error_since']))}",
                    f"Erreur : {html.escape(error[:300])}",
                    f"Fallback : {html.escape(fallback)}",
                ]
            )
            if send_telegram_message(message):
                current["alerted"] = True
                current["last_alert"] = now_iso()
    else:
        if previous.get("alerted") and not dry_run:
            send_telegram_message(
                f"<b>✅ {html.escape(store.upper())} monitor rétabli</b>\n\n"
                f"Les vérifications répondent de nouveau normalement."
            )
        current.update(
            {
                "consecutive_errors": 0,
                "last_error": None,
                "error_since": None,
                "alerted": False,
            }
        )
    store_states[key] = current


def update_route_health(
    item: dict[str, Any],
    functional: bool,
    detail: str,
    route_states: dict[str, Any],
) -> None:
    route_states[route_key(item)] = {
        "id": str(item.get("id") or product_key(item)),
        "store": str(item.get("store", "Boutique")),
        "name": str(item.get("name", "Voie sans nom")),
        "kind": str(item.get("type", "product")),
        "functional": bool(functional),
        "detail": detail[:300],
        "last_check": now_iso(),
    }


def classify_canary(product: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Classe un témoin sans déclencher d'alerte commerciale."""
    http_status = result.get("http_status")
    error = result.get("error")
    status = result.get("status", "unknown")
    price = result.get("price")
    reason = str(result.get("reason") or error or "Résultat incomplet")
    if error or (isinstance(http_status, int) and (http_status in (403, 429) or http_status >= 500)):
        level, label = "blind", "🔴 non vérifiable"
    elif status == "available" and price is not None:
        level = "verified"
        label = "🟢 API + prix confirmés" if reason.startswith("API ") else "🟢 disponibilité + prix confirmés"
    else:
        level = "partial"
        label = "🟠 page accessible, achat non confirmé"
    return {
        "id": str(product.get("id") or product_key(product)),
        "store": str(product["store"]),
        "name": str(product["name"]),
        "url": str(product["url"]),
        "level": level,
        "label": label,
        "status": status,
        "price": price,
        "http_status": http_status,
        "detail": reason[:300],
        "last_check": now_iso(),
    }


def run_canaries() -> int:
    canaries = load_json(config.CANARIES_FILE, [])
    if not isinstance(canaries, list):
        logging.error("canaries.json doit contenir une liste JSON")
        return 2
    active = [item for item in canaries if isinstance(item, dict) and item.get("enabled", True)]
    invalid = [validate_product(item, index) for index, item in enumerate(active, start=1)]
    if any(invalid):
        for error in (value for value in invalid if value):
            logging.error("Témoin invalide: %s", error)
        return 2
    state: dict[str, Any] = {"version": 1, "last_scan": now_iso(), "canaries": {}}
    with ThreadPoolExecutor(max_workers=min(config.MAX_WORKERS, len(active) or 1)) as executor:
        futures = {executor.submit(check_product, product): product for product in active}
        for future in as_completed(futures):
            product = futures[future]
            try:
                _, result = future.result()
            except Exception as exc:
                result = {
                    "status": "unknown",
                    "price": None,
                    "error": f"{type(exc).__name__}: {exc}",
                    "reason": str(exc),
                }
            value = classify_canary(product, result)
            state["canaries"][str(product["store"]).strip().lower()] = value
            logging.info("[TÉMOIN %s] %s — %s", product["store"].upper(), value["label"], value["detail"])
            # L'état est visible même si un autre navigateur prend du temps.
            save_json_atomic(config.CANARY_STATE_FILE, state)
    state["last_scan"] = now_iso()
    save_json_atomic(config.CANARY_STATE_FILE, state)
    return 0


def send_health_report() -> bool:
    products = load_json(config.PRODUCTS_FILE, [])
    enabled = [item for item in products if isinstance(item, dict) and item.get("enabled", True)]
    sites = sorted({str(item.get("store", "")).strip() for item in enabled if item.get("store")})
    configured_products = [item for item in enabled if item.get("type", "product") == "product"]
    discovered = load_json(config.DISCOVERED_PRODUCTS_FILE, []) if config.DISCOVERED_PRODUCTS_FILE else []
    discovered = discovered if isinstance(discovered, list) else []

    latest_scan: str | None = None
    errors: dict[str, dict[str, Any]] = {}
    route_states: dict[str, dict[str, Any]] = {}
    for path in config.HEALTH_STATE_FILES:
        state = load_json(path, {})
        scan = state.get("last_scan") if isinstance(state, dict) else None
        if scan and (latest_scan is None or str(scan) > latest_scan):
            latest_scan = str(scan)
        for key, value in (state.get("store_health", {}) if isinstance(state, dict) else {}).items():
            if isinstance(value, dict) and int(value.get("consecutive_errors", 0)) > 0:
                existing = errors.get(key)
                if not existing or int(value.get("consecutive_errors", 0)) > int(existing.get("consecutive_errors", 0)):
                    errors[key] = value
        for key, value in (state.get("route_health", {}) if isinstance(state, dict) else {}).items():
            if not isinstance(value, dict):
                continue
            existing = route_states.get(key)
            if not existing or str(value.get("last_check", "")) > str(existing.get("last_check", "")):
                route_states[key] = value

    last_scan = "aucun scan enregistré"
    scan_is_fresh = False
    scan_age_seconds: int | None = None
    if latest_scan:
        try:
            scan_time = datetime.fromisoformat(latest_scan).astimezone(ZoneInfo(config.TIMEZONE))
            scan_age_seconds = max(0, int((paris_now() - scan_time).total_seconds()))
            scan_is_fresh = scan_age_seconds <= config.HEALTH_STALE_AFTER_SECONDS
            last_scan = scan_time.strftime("%d/%m/%Y %H:%M:%S")
        except ValueError:
            last_scan = latest_scan
    age_text = "inconnu"
    if scan_age_seconds is not None:
        age_text = f"il y a {scan_age_seconds} s" if scan_age_seconds < 120 else f"il y a {scan_age_seconds // 60} min"
    target_ean = "0196214144835"
    exact_ean = [item for item in configured_products if str(item.get("ean", "")) == target_ean]
    ean_sources = [
        item for item in enabled
        if item.get("type", "product") != "product" and target_ean in [str(value) for value in item.get("eans", [])]
    ]
    category_sources = [
        item for item in ean_sources if item.get("type") in ("category_search", "sitemap")
    ]
    functional_routes = 0
    verifiable_stores: set[str] = set()
    for item in enabled:
        route = route_states.get(route_key(item), {})
        try:
            checked_at = datetime.fromisoformat(str(route["last_check"])).astimezone(ZoneInfo(config.TIMEZONE))
            max_age = (
                config.PRODUCT_ROUTE_STALE_SECONDS
                if item.get("type", "product") == "product"
                else config.DISCOVERY_ROUTE_STALE_SECONDS
            )
            is_functional = bool(route.get("functional")) and (paris_now() - checked_at).total_seconds() <= max_age
        except (KeyError, TypeError, ValueError):
            is_functional = False
        if is_functional:
            functional_routes += 1
            verifiable_stores.add(str(item.get("store", "")).strip())
    route_blind_stores = [store for store in sites if store not in verifiable_stores]
    confidence_percent = round(100 * functional_routes / len(enabled)) if enabled else 0
    canary_config = load_json(config.CANARIES_FILE, [])
    canary_config = [item for item in canary_config if isinstance(item, dict) and item.get("enabled", True)] \
        if isinstance(canary_config, list) else []
    canary_state = load_json(config.CANARY_STATE_FILE, {})
    canary_results = canary_state.get("canaries", {}) if isinstance(canary_state, dict) else {}
    canary_lines: list[str] = []
    verified_canary_stores: set[str] = set()
    blind_canary_stores: list[str] = []
    for item in sorted(canary_config, key=lambda value: str(value.get("store", ""))):
        store = str(item.get("store", "Boutique"))
        result = canary_results.get(store.strip().lower(), {})
        fresh = False
        try:
            checked_at = datetime.fromisoformat(str(result["last_check"])).astimezone(ZoneInfo(config.TIMEZONE))
            fresh = (paris_now() - checked_at).total_seconds() <= config.CANARY_STALE_SECONDS
        except (KeyError, TypeError, ValueError):
            pass
        if not fresh:
            label = "🔴 non vérifié récemment"
            blind_canary_stores.append(store)
        else:
            label = str(result.get("label") or "🔴 non vérifiable")
            if result.get("level") == "verified":
                verified_canary_stores.add(store)
            elif result.get("level") == "blind":
                blind_canary_stores.append(store)
        canary_lines.append(f"• {html.escape(store)} : {html.escape(label)}")
    report_verifiable = verified_canary_stores if canary_config else verifiable_stores
    report_total = len(canary_config) if canary_config else len(sites)
    blind_stores = blind_canary_stores if canary_config else route_blind_stores
    telegram_healthy = test_telegram_connection()
    restart_requested = False
    if not scan_is_fresh and config.AUTO_RESTART_STALE:
        restart_requested = restart_local_services()
    lines = [
        "<b>✅ Pokémon Monitor opérationnel</b>" if scan_is_fresh else "<b>🔴 Pokémon Monitor en retard</b>",
        "",
        f"Bot vivant : {'✅' if scan_is_fresh else '❌'}",
        f"Boutiques vérifiables : {len(report_verifiable)}/{report_total}",
        f"Voies fonctionnelles : {functional_routes}/{len(enabled)} ({confidence_percent} %)",
        f"Boutiques aveugles : {html.escape(', '.join(blind_stores) if blind_stores else 'aucune')}",
        f"Dernier test d'alerte : {'✅' if telegram_healthy else '❌'}",
        "",
        f"🏪 Sites surveillés : {len(sites)}",
        f"📦 Produits : {len(configured_products) + len(discovered)}",
        f"🕒 Dernier scan : {html.escape(last_scan)} ({age_text})",
        f"🎯 ETB {target_ean} : {len(exact_ean)} URL(s) exacte(s), {len(ean_sources)} source(s) EAN, {len(category_sources)} catégorie(s)/sitemap(s)",
        f"⚠️ Erreurs actives : {len(errors)}",
    ]
    if canary_lines:
        lines.extend(["", "<b>Produits témoins</b>", *canary_lines])
    if not scan_is_fresh:
        lines.append("⚠️ Le scan rapide devrait dater de moins de 3 minutes.")
        lines.append(
            "🔄 Relance automatique demandée." if restart_requested
            else "❌ La relance automatique n'a pas pu être confirmée."
        )
    for value in sorted(errors.values(), key=lambda item: str(item.get("store", "")))[:8]:
        lines.append(
            f"• {html.escape(str(value.get('store', 'Site')))} : "
            f"{int(value.get('consecutive_errors', 0))} échec(s) — "
            f"{html.escape(str(value.get('last_error') or 'erreur inconnue'))}"
        )
    reply_markup = None
    if not scan_is_fresh:
        reply_markup = {
            "inline_keyboard": [[{"text": "🔄 Tout relancer", "callback_data": RESTART_CALLBACK_DATA}]]
        }
    return send_telegram_message("\n".join(lines), reply_markup=reply_markup)


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


def check_products_batch(
    products: list[dict[str, Any]],
    product_states: dict[str, Any],
    store_states: dict[str, Any],
    route_states: dict[str, Any],
    test_mode: bool,
) -> None:
    if not products:
        return
    outcomes: dict[str, list[str | None]] = {}
    with ThreadPoolExecutor(max_workers=min(config.MAX_WORKERS, len(products))) as executor:
        futures = {executor.submit(check_product, product): product for product in products}
        for future in as_completed(futures):
            product = futures[future]
            try:
                _, result = future.result()
            except Exception as exc:
                logging.exception("[%s] %s → échec isolé: %s", product["store"], product["name"], exc)
                update_route_health(product, False, f"{type(exc).__name__}: {exc}", route_states)
                outcomes.setdefault(str(product["store"]), []).append(
                    f"{type(exc).__name__}: {exc}"[:300]
                )
                continue
            log_result(product, result)
            functional = not result.get("error") and result.get("status") in ("available", "unavailable")
            update_route_health(
                product,
                functional,
                str(result.get("reason") or result.get("error") or result.get("status", "inconnu")),
                route_states,
            )
            outcomes.setdefault(str(product["store"]), []).append(result.get("error"))
            key = product_key(product)
            product_states[key] = updated_state_and_alert(
                product, result, product_states.get(key, {}), test_mode
            )
    for store, values in outcomes.items():
        failures = [value for value in values if value]
        error = failures[0] if failures and len(failures) == len(values) else None
        update_store_health(store, error, store_states, test_mode, "Fallback HTML/Playwright actif")


def run(test_mode: bool = False, products_only: bool = False, discovery_only: bool = False) -> int:
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

    state = load_json(config.STATE_FILE, {"version": 1, "products": {}})
    old_state = json.dumps(state, sort_keys=True)
    product_states = state.setdefault("products", {})
    store_states = state.setdefault("store_health", {})
    route_states = state.setdefault("route_health", {})

    # Priorité au stock des fiches connues : une exploration de gros sitemaps
    # ne doit jamais retarder le contrôle des boutons « Ajouter au panier ».
    direct = [item for item in active if item.get("type", "product") == "product"]
    if products_only and config.DISCOVERED_PRODUCTS_FILE:
        cached_products = load_json(config.DISCOVERED_PRODUCTS_FILE, [])
        if isinstance(cached_products, list):
            by_url = {product["url"]: product for product in direct}
            for product in cached_products:
                if isinstance(product, dict) and product.get("url"):
                    by_url.setdefault(product["url"], product)
            direct = list(by_url.values())
    if not discovery_only:
        check_products_batch(direct, product_states, store_states, route_states, test_mode)

    discovered: list[dict[str, Any]] = []
    if not products_only:
        expanded = expand_discovery_sources(active, state, test_mode)
        direct_urls = {product["url"] for product in direct}
        discovered = [product for product in expanded if product["url"] not in direct_urls]
        check_products_batch(discovered, product_states, store_states, route_states, test_mode)
        if config.DISCOVERED_PRODUCTS_FILE and not test_mode:
            save_json_atomic(config.DISCOVERED_PRODUCTS_FILE, discovered)

    if config.RECORD_HEALTH and not test_mode:
        state["last_scan"] = now_iso()

    checked_count = (0 if discovery_only else len(direct)) + len(discovered)
    if checked_count == 0:
        logging.warning("Aucun produit actif avec une URL réelle dans products.json")
        if not test_mode and json.dumps(state, sort_keys=True) != old_state:
            save_json_atomic(config.STATE_FILE, state)
        return 0

    if test_mode:
        logging.info("Mode test: aucune alerte envoyée, state.json non modifié")
    if not test_mode and json.dumps(state, sort_keys=True) != old_state:
        save_json_atomic(config.STATE_FILE, state)
        logging.info("state.json mis à jour")
    elif not test_mode:
        logging.info("Aucun changement d'état")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Moniteur Pokémon 30e anniversaire")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--once", action="store_true", help="effectue un scan normal")
    modes.add_argument("--test", action="store_true", help="analyse sans alerte ni écriture")
    modes.add_argument("--test-telegram", action="store_true", help="teste uniquement Telegram")
    modes.add_argument("--products-only", action="store_true", help="contrôle uniquement les fiches connues")
    modes.add_argument("--discovery-only", action="store_true", help="cherche et contrôle uniquement les nouvelles fiches")
    modes.add_argument("--health-report", action="store_true", help="envoie le rapport de santé quotidien")
    modes.add_argument("--canaries-only", action="store_true", help="contrôle les produits témoins sans alerte stock")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.test_telegram:
        return 0 if send_telegram_message("✅ Bot Pokémon opérationnel.") else 1
    if args.health_report:
        return 0 if send_health_report() else 1
    if args.canaries_only:
        return run_canaries()
    return run(
        test_mode=args.test,
        products_only=args.products_only,
        discovery_only=args.discovery_only,
    )


if __name__ == "__main__":
    sys.exit(main())
