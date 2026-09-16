"""Configuration centrale du moniteur."""

from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
PRODUCTS_FILE = BASE_DIR / "products.json"
STATE_FILE = Path(os.getenv("STATE_FILE", str(BASE_DIR / "state.json"))).expanduser()
_discovered_file = os.getenv("DISCOVERED_PRODUCTS_FILE", "")
DISCOVERED_PRODUCTS_FILE = Path(_discovered_file).expanduser() if _discovered_file else None
HEALTH_STATE_FILES = [
    Path(value).expanduser()
    for value in os.getenv("HEALTH_STATE_FILES", str(STATE_FILE)).split(":")
    if value
]
RECORD_HEALTH = os.getenv("RECORD_HEALTH", "false").lower() in {"1", "true", "yes"}
HEALTH_STALE_AFTER_SECONDS = int(os.getenv("HEALTH_STALE_AFTER_SECONDS", "180"))
PRODUCT_ROUTE_STALE_SECONDS = int(os.getenv("PRODUCT_ROUTE_STALE_SECONDS", "180"))
DISCOVERY_ROUTE_STALE_SECONDS = int(os.getenv("DISCOVERY_ROUTE_STALE_SECONDS", "1800"))
AUTO_RESTART_STALE = os.getenv("AUTO_RESTART_STALE", "false").lower() in {"1", "true", "yes"}

TIMEZONE = "Europe/Paris"
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "5"))
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))
REQUEST_RETRIES = int(os.getenv("REQUEST_RETRIES", "2"))
REQUEST_BACKOFF_SECONDS = float(os.getenv("REQUEST_BACKOFF_SECONDS", "1"))
ALERT_EXPENSIVE_PRODUCTS = os.getenv("ALERT_EXPENSIVE_PRODUCTS", "false").lower() in {
    "1",
    "true",
    "yes",
}

USER_AGENT = os.getenv(
    "USER_AGENT",
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
