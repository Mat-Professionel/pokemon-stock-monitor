"""Profils par enseigne. Les sélecteurs peuvent être ajustés sans toucher au moteur."""

from .generic import GenericMonitor


class SmythsProfile(GenericMonitor):
    direct_seller_aliases = ("Smyths",)


class LeclercProfile(GenericMonitor):
    marketplace = True
    direct_seller_aliases = ("E.Leclerc", "Leclerc")


class FnacProfile(GenericMonitor):
    marketplace = True
    direct_seller_aliases = ("Fnac",)


class CarrefourProfile(GenericMonitor):
    marketplace = True
    direct_seller_aliases = ("Carrefour",)


class CulturaProfile(GenericMonitor):
    direct_seller_aliases = ("Cultura",)


class KingJouetProfile(GenericMonitor):
    direct_seller_aliases = ("King Jouet",)


class AmazonProfile(GenericMonitor):
    marketplace = True
    direct_seller_aliases = ("Amazon", "Amazon.fr")
    positive_signals = GenericMonitor.positive_signals + ("ajouter au panier", "en stock")


class CdiscountProfile(GenericMonitor):
    marketplace = True
    direct_seller_aliases = ("Cdiscount",)


class AuchanProfile(GenericMonitor):
    marketplace = True
    direct_seller_aliases = ("Auchan",)


class MicromaniaProfile(GenericMonitor):
    direct_seller_aliases = ("Micromania", "Zing")


class JoueClubProfile(GenericMonitor):
    direct_seller_aliases = ("JouéClub", "JoueClub")


class LaGrandeRecreProfile(GenericMonitor):
    direct_seller_aliases = ("La Grande Récré",)


class PhilibertProfile(GenericMonitor):
    direct_seller_aliases = ("Philibert",)

