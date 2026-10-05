from __future__ import annotations

from typing import Protocol

from ..config import Config
from ..modeles import Creneau


class LigneIntrouvable(Exception):
    """La ligne de la personne suivie n'apparaît pas dans le planning lu."""


class Connecteur(Protocol):
    def recuperer(self, debut, fin) -> list[Creneau]:
        """Créneaux de la personne suivie entre deux dates incluses, collègues déjà écartés.

        Lève LigneIntrouvable si sa ligne est absente.
        """
        ...


def creer_connecteur(cfg: Config) -> Connecteur:
    if cfg.connecteur == "silae":
        from .silae import ConnecteurSilae

        return ConnecteurSilae(cfg.platform_user, cfg.platform_password, cfg.person_match)
    if cfg.connecteur == "fichier":
        from .fichier import ConnecteurFichier

        return ConnecteurFichier(cfg.fichier_planning)
    raise ValueError(f"Connecteur inconnu : {cfg.connecteur}")
