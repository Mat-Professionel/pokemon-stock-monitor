#!/usr/bin/env python3
"""Traite les boutons Telegram autorisés sans exposer de commande générique."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import requests

import config
from local_control import RESTART_CALLBACK_DATA, restart_local_services
from monitor import load_json, save_json_atomic, send_telegram_message


CONTROL_STATE_FILE = Path(
    os.getenv("TELEGRAM_CONTROL_STATE_FILE", str(config.BASE_DIR / "telegram-control-state.json"))
).expanduser()


def answer_callback(callback_id: str, text: str, show_alert: bool = False) -> None:
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text, "show_alert": show_alert},
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        ).raise_for_status()
    except requests.RequestException as exc:
        logging.error("Impossible de confirmer le bouton Telegram: %s", type(exc).__name__)


def callback_chat_id(query: dict[str, Any]) -> str:
    return str(query.get("message", {}).get("chat", {}).get("id", ""))


def handle_callback(query: dict[str, Any]) -> None:
    callback_id = str(query.get("id", ""))
    if callback_chat_id(query) != str(config.TELEGRAM_CHAT_ID):
        answer_callback(callback_id, "Commande non autorisée.", show_alert=True)
        return
    if query.get("data") != RESTART_CALLBACK_DATA:
        answer_callback(callback_id, "Commande inconnue.", show_alert=True)
        return

    success = restart_local_services()
    if success:
        answer_callback(callback_id, "Relance demandée.")
        send_telegram_message(
            "<b>🔄 Relance manuelle demandée</b>\n\n"
            "Le scan produits et la découverte redémarrent. "
            "Le prochain rapport confirmera leur état."
        )
    else:
        answer_callback(callback_id, "La relance a échoué.", show_alert=True)
        send_telegram_message(
            "<b>❌ Relance manuelle impossible</b>\n\n"
            "Le Mac doit être allumé, connecté et la session utilisateur ouverte."
        )


def run_once() -> int:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logging.error("Configuration Telegram absente")
        return 2
    state = load_json(CONTROL_STATE_FILE, {"offset": 0})
    offset = int(state.get("offset", 0))
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates",
            params={"offset": offset, "limit": 20, "allowed_updates": '["callback_query"]'},
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError("Telegram a refusé getUpdates")
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        logging.error("Lecture des boutons Telegram impossible: %s", type(exc).__name__)
        return 1

    updates = payload.get("result", [])
    for update in updates:
        update_id = int(update.get("update_id", offset - 1))
        offset = max(offset, update_id + 1)
        query = update.get("callback_query")
        if isinstance(query, dict):
            handle_callback(query)
    save_json_atomic(CONTROL_STATE_FILE, {"offset": offset})
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(run_once())
