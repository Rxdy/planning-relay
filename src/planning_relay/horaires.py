"""Horaires en dur (CONNECTOR=horaires) : les postes de Rudy, Léa, Luis et Caroline.

Contrairement à Silae, la source est un fichier du dépôt (donnees/horaires.json,
format repris d'AbView) : l'agenda en est la copie exacte. Une modification
n'est pas marquée « (modifié) », un poste retiré du fichier est retiré de
l'agenda, et aucun mail n'est envoyé.

Calcul repris d'AbView (calendarStore.ts) pour ne rien changer à l'affichage :
- « fixed » : mêmes horaires chaque semaine, par jour ;
- « shift » : rotation sur plusieurs semaines. La position dans le cycle vient
  de `cycleAnchor` (semaine de référence) ou, à défaut, de `offset`. Une semaine
  de `weekOverrides` remplace la rotation, et `exceptions` remplace les horaires
  d'un jour (une liste vide : pas de poste ce jour-là).
Les semaines sont numérotées à l'ISO, combinées avec l'année civile, comme dans AbView.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .comparaison import comparer
from .config import Config
from .modeles import Changement, Creneau
from .synchro import etape, fenetre

log = logging.getLogger("planning_relay")

JOURS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
TYPES = {"fixed", "shift"}


def charger(chemin: str | Path) -> list[dict]:
    regles = json.loads(Path(chemin).read_text(encoding="utf-8"))
    for p in regles:
        if not p.get("name") or p.get("type") not in TYPES:
            raise ValueError(f"Personne invalide dans {chemin} : {p.get('name')!r} ({p.get('type')!r})")
    return regles


def cle(nom: str) -> str:
    """« Léa » → « horaires-lea » : propriété privée des événements de cette personne."""
    sans_accents = unicodedata.normalize("NFD", nom).encode("ascii", "ignore").decode()
    return "horaires-" + "-".join(sans_accents.lower().split())


def _position(personne: dict, jour: date, semaine: int) -> int:
    n = personne.get("cycleLength") or len(personne.get("rotation") or []) or 1
    if ancre := personne.get("cycleAnchor"):
        ecart = (jour.year * 52 + semaine) - (ancre["year"] * 52 + ancre["week"])
        return (ancre["position"] + ecart) % n
    return (semaine + personne.get("offset", 0)) % n


def horaires_du_jour(personne: dict, jour: date) -> tuple[str, list[str]]:
    """(poste, ["HH:MM-HH:MM", ...]) ; poste vide pour des horaires fixes."""
    nom_jour = JOURS[jour.weekday()]
    if personne["type"] == "fixed":
        return "", personne.get("schedule", {}).get(nom_jour) or []

    semaine = jour.isocalendar().week
    rotation = personne.get("rotation") or []
    courant = (personne.get("weekOverrides") or {}).get(f"{jour.year}-{semaine}")
    if courant is None:
        position = _position(personne, jour, semaine)
        courant = rotation[position] if position < len(rotation) else None
    if not courant:
        return "", []
    exceptions = courant.get("exceptions") or {}
    heures = exceptions[nom_jour] if exceptions.get(nom_jour) is not None else courant.get("hours")
    return courant.get("shift", ""), heures or []


def creneaux(personne: dict, debut: date, fin: date) -> list[Creneau]:
    """Un créneau par jour travaillé entre deux dates incluses."""
    resultat = []
    jour = debut
    while jour <= fin:
        poste, heures = horaires_du_jour(personne, jour)
        if len(heures) > 1:
            raise ValueError(f"{personne['name']} : un seul créneau par jour ({jour}, {heures})")
        if heures:
            h_debut, h_fin = heures[0].split("-")
            resultat.append(Creneau(
                jour=jour,
                code=poste or "Travail",
                debut=time.fromisoformat(h_debut),
                fin=time.fromisoformat(h_fin),
                poste=poste,
            ))
        jour += timedelta(days=1)
    return resultat


def passage_horaires(cfg: Config, personne: dict, agenda, aujourdhui: date) -> list[Changement]:
    """Rend l'agenda identique aux horaires d'une personne, d'aujourd'hui à la fin de la fenêtre."""
    debut, fin = fenetre(aujourdhui, cfg.semaines)
    lus = {c.jour: c for c in creneaux(personne, debut, fin)}
    with etape("Google Agenda"):
        connus = agenda.lister(debut, fin)
        if not cfg.dry_run:
            agenda.ajouter_categories()
            agenda.rafraichir_titres()
    changements = comparer(connus, lus)
    log.info("%s : %d poste(s), %d changement(s)", cfg.personne, len(lus), len(changements))
    if cfg.dry_run:
        for ch in changements:
            log.info("[dry-run] %s %s %s", cfg.personne, ch.type, ch.jour)
        return changements
    with etape("Google Agenda"):
        agenda.appliquer(changements, aujourdhui, exact=True)
    return changements


def synchro_horaires(cfg: Config, service=None, aujourdhui: date | None = None) -> int:
    """Un passage pour toutes les personnes du fichier. Pas de mail : la santé du
    conteneur (compteur d'échecs) suffit à signaler une panne."""
    from .agenda import Agenda
    from .alerte import Compteur

    maintenant = datetime.now(ZoneInfo(cfg.fuseau))
    aujourdhui = aujourdhui or maintenant.date()
    compteur = Compteur(cfg.dossier_etat)
    try:
        for personne in charger(cfg.fichier_horaires):
            pcfg = replace(cfg, personne=personne["name"], cle_synchro=cle(personne["name"]))
            agenda = Agenda(pcfg, service=service)
            service = agenda.service  # une seule connexion pour tout le passage
            passage_horaires(pcfg, personne, agenda, aujourdhui)
    except Exception as e:
        n = compteur.echec(maintenant)
        # Le type seul : le texte brut d'une erreur peut contenir des données.
        log.error("Passage en échec (%s : %s), %d échec(s) d'affilée",
                  getattr(e, "etape", "Horaires"), type(e).__name__, n)
        return 1
    compteur.succes()
    return 0
