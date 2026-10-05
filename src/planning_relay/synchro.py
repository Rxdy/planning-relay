"""Un passage horaire : récupérer, filtrer et comparer, prévenir, publier."""

from __future__ import annotations

import logging
from contextlib import contextmanager
import time as horloge
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .comparaison import comparer
from .config import Config
from .modeles import Changement, Creneau

log = logging.getLogger("planning_relay")


class GardeFou(Exception):
    """Lecture suspecte : on s'arrête sans rien toucher dans l'agenda."""


@contextmanager
def etape(nom: str):
    """Note sur l'erreur l'étape où elle s'est produite (lue par le diagnostic)."""
    try:
        yield
    except Exception as e:
        if not hasattr(e, "etape"):
            e.etape = nom
        raise


def fenetre(aujourdhui: date, semaines: int) -> tuple[date, date]:
    """Aujourd'hui → dimanche de la dernière semaine regardée.

    Le planning n'est publié qu'une semaine ou deux à l'avance : la fenêtre est
    large pour tout prendre dès publication, et les jours sans rien restent vides.
    """
    lundi = aujourdhui - timedelta(days=aujourdhui.weekday())
    return aujourdhui, lundi + timedelta(weeks=semaines, days=-1)


def recuperer_avec_essais(connecteur, debut: date, fin: date, essais: int, delai: int) -> list[Creneau]:
    for essai in range(1, essais + 1):
        try:
            return connecteur.recuperer(debut, fin)
        except Exception as e:
            if essai == essais:
                raise
            # Ne logguer que le type : un message d'erreur HTTP peut contenir des données.
            log.warning("Lecture échouée (%s), essai %d/%d, nouvel essai dans %d s",
                        type(e).__name__, essai, essais, delai)
            horloge.sleep(delai)
    raise AssertionError("inatteignable")


def passage(cfg: Config, connecteur, agenda, messagerie, aujourdhui: date | None = None) -> list[Changement]:
    aujourdhui = aujourdhui or datetime.now(ZoneInfo(cfg.fuseau)).date()
    debut, fin = fenetre(aujourdhui, cfg.semaines)
    lundi = debut - timedelta(days=debut.weekday())

    # Depuis lundi : les jours passés de la semaine ne sont pas comparés, mais
    # le mail montre la semaine entière.
    with etape("Silae"):
        lus_bruts = recuperer_avec_essais(connecteur, lundi, fin, cfg.essais, cfg.delai_essai)
    planning = {c.jour: c for c in lus_bruts if lundi <= c.jour <= fin and c.code not in cfg.codes_ignores}
    lus = {j: c for j, c in planning.items() if j >= debut}
    with etape("Google Agenda"):
        connus = agenda.lister(debut, fin)
    log.info("Créneaux lus : %d ; déjà dans l'agenda : %d", len(lus), len(connus))

    if not lus_bruts and connus:
        with etape("Silae"):
            raise GardeFou("Planning vide alors que l'agenda en contient : rien n'est supprimé")

    if not cfg.dry_run:
        # Titres écrits par une version précédente : mis à jour sans mail.
        with etape("Google Agenda"):
            n = agenda.rafraichir_titres()
        if n:
            log.info("Titres remis au format actuel : %d", n)

    changements = comparer(connus, lus)
    log.info("Changements : %d", len(changements))
    if not changements:
        return []
    if cfg.dry_run:
        for ch in changements:
            log.info("[dry-run] %s %s", ch.type, ch.jour)
        return changements

    # Mail d'abord : si l'écriture échoue, le passage suivant renverra le mail
    # plutôt que de perdre le changement.
    with etape("Gmail"):
        messagerie.par_semaine(planning, connus, changements, aujourdhui)
    with etape("Google Agenda"):
        agenda.appliquer(changements, aujourdhui)
    return changements
