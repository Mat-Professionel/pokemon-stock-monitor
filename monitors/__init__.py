"""Registre des moniteurs par enseigne."""

from __future__ import annotations

from .amazon import AmazonMonitor
from .auchan import AuchanMonitor
from .carrefour import CarrefourMonitor
from .cdiscount import CdiscountMonitor
from .cultura import CulturaMonitor
from .fnac import FnacMonitor
from .generic import GenericMonitor
from .joueclub import JoueClubMonitor
from .kingjouet import KingJouetMonitor
from .lagranderecre import LaGrandeRecreMonitor
from .leclerc import LeclercMonitor
from .micromania import MicromaniaMonitor
from .philibert import PhilibertMonitor
from .smyths import SmythsMonitor


MONITORS = {
    "smyths": SmythsMonitor,
    "smyths toys": SmythsMonitor,
    "e.leclerc": LeclercMonitor,
    "leclerc": LeclercMonitor,
    "fnac": FnacMonitor,
    "carrefour": CarrefourMonitor,
    "cultura": CulturaMonitor,
    "king jouet": KingJouetMonitor,
    "amazon": AmazonMonitor,
    "amazon france": AmazonMonitor,
    "cdiscount": CdiscountMonitor,
    "auchan": AuchanMonitor,
    "micromania": MicromaniaMonitor,
    "jouéclub": JoueClubMonitor,
    "joueclub": JoueClubMonitor,
    "la grande récré": LaGrandeRecreMonitor,
    "la grande recre": LaGrandeRecreMonitor,
    "philibert": PhilibertMonitor,
}


def monitor_for(product, session):
    monitor_class = MONITORS.get(product["store"].strip().lower(), GenericMonitor)
    return monitor_class(session=session, engine=product.get("engine", "requests"))

