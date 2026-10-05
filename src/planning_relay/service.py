"""Conteneur permanent : un passage à chaque heure pile (hh:00, heure de Paris).

Comme les autres services du Pi, le conteneur tourne en continu (visible dans
metryx, mis à jour par Watchtower). Pas de passage au démarrage : un
redémarrage ne doit pas ajouter de connexion à sirh.software.

La santé du service, lue par le HEALTHCHECK Docker, vient de deux fichiers du
volume /data : l'heure du dernier passage et le compteur d'échecs.
"""

from __future__ import annotations

import logging
import signal
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .alerte import SEUIL, Compteur
from .config import Config

log = logging.getLogger("planning_relay")

# Au-delà, le planificateur est considéré comme bloqué
RETARD_MAX = timedelta(hours=2, minutes=30)


def prochaine_heure(maintenant: datetime) -> datetime:
    return maintenant.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def _fichier_dernier(cfg: Config) -> Path:
    return Path(cfg.dossier_etat) / "dernier_passage"


def sante(cfg: Config, maintenant: datetime | None = None) -> tuple[bool, str]:
    maintenant = maintenant or datetime.now(ZoneInfo(cfg.fuseau))
    echecs = Compteur(cfg.dossier_etat).lire()
    if echecs >= SEUIL:
        return False, f"{echecs} échecs d'affilée"
    try:
        dernier = datetime.fromisoformat(_fichier_dernier(cfg).read_text().strip())
    except (FileNotFoundError, ValueError):
        return True, "aucun passage depuis le démarrage"
    if maintenant - dernier > RETARD_MAX:
        return False, f"dernier passage le {dernier:%d/%m à %H:%M}"
    return True, f"dernier passage à {dernier:%H:%M}, {echecs} échec(s)"


def tourner(cfg: Config, passage, arret: threading.Event, horloge=None) -> None:
    """Boucle jusqu'à `arret` : attendre hh:00, faire un passage, recommencer."""
    tz = ZoneInfo(cfg.fuseau)
    horloge = horloge or (lambda: datetime.now(tz))
    while not arret.is_set():
        cible = prochaine_heure(horloge())
        log.info("Prochain passage à %s", cible.strftime("%H:%M"))
        # wait() rend la main dès l'arrêt du conteneur (SIGTERM)
        while not arret.is_set() and (reste := (cible - horloge()).total_seconds()) > 0:
            arret.wait(min(reste, 60))
        if arret.is_set():
            break
        try:
            passage(cfg)
        except Exception as e:  # passage() gère déjà ses erreurs ; filet de sécurité
            log.error("Erreur inattendue (%s)", type(e).__name__)
        fichier = _fichier_dernier(cfg)
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_text(horloge().isoformat())


def lancer(cfg: Config, passage) -> int:
    arret = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: arret.set())
    log.info("Service démarré : un passage à chaque heure pile")
    tourner(cfg, passage, arret)
    log.info("Service arrêté")
    return 0
