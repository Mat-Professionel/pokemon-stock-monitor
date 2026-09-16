"""Commandes locales strictement limitées aux services du moniteur."""

from __future__ import annotations

import logging
import os
import subprocess


RESTART_CALLBACK_DATA = "restart_all"
LOCAL_SERVICE_LABELS = (
    "com.leo.pokemon-stock-monitor",
    "com.leo.pokemon-stock-monitor.discovery",
)


def restart_local_services() -> bool:
    """Redémarre uniquement les deux scanners launchd de l'utilisateur courant."""
    domain = f"gui/{os.getuid()}"
    success = True
    for label in LOCAL_SERVICE_LABELS:
        try:
            result = subprocess.run(
                ["/bin/launchctl", "kickstart", "-k", f"{domain}/{label}"],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logging.error("Relance locale impossible pour %s: %s", label, type(exc).__name__)
            success = False
            continue
        if result.returncode != 0:
            logging.error("Relance locale refusée pour %s (code %s)", label, result.returncode)
            success = False
    return success
