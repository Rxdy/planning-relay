"""Inscriptions des enfants (CONNECTOR=kidsync) : la MJC, lue dans le fichier de kidsync.

kidsync (~/dev/kidsync) relève le planning du portail MJC et écrit `data/inscriptions.json` :
une entrée par (date, enfant, activité) avec un statut `prevu`, `reserve` ou `ecart`.
Ici, seules les inscriptions `reserve` vont dans l'agenda : un écart ou un jour jamais relevé
n'est pas une inscription. L'agenda est la copie exacte du fichier (comme `horaires`) :
une inscription qui repasse en écart est retirée, et aucun mail n'est envoyé.

Chaque enfant a sa propre clé de synchro (`planning_relay=kidsync-lyam`) et son titre
« Lyam — MJC » (mercredis) ou « Lyam — Périscolaire ». Au plus un événement par enfant et par jour :
si deux activités tombent le même jour, elles sont réunies (heure de début la plus tôt,
heure de fin la plus tard).

Source : le serveur de kidsync (KIDSYNC_URL + KIDSYNC_TOKEN), que le conteneur interroge à chaque
passage, ou un fichier local (KIDSYNC_FILE). Si le serveur est injoignable, le passage ne change rien
et n'est pas compté comme un échec.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import replace
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .comparaison import comparer
from .config import Config
from .modeles import Changement, Creneau
from .synchro import etape, fenetre

log = logging.getLogger("planning_relay")

# Libellés des créneaux du portail MJC -> (début, fin)
HORAIRES = {
    "Matin": (time(7, 30), time(12, 0)),
    "Repas": (time(12, 0), time(13, 30)),
    "Après-midi": (time(13, 30), time(19, 0)),
    "16h30-17h30": (time(16, 30), time(17, 30)),
    "17h30-19h00": (time(17, 30), time(19, 0)),
}
POSTES = {"mercredis": "MJC", "periscolaire": "Périscolaire"}
CATEGORIE = "periscolaire"  # couleur propre dans AbView ; les mercredis MJC en font partie


class SourceInjoignable(Exception):
    """Le serveur de kidsync ne répond pas (le poste est éteint, par exemple)."""


def _valider(brut, origine: str) -> list[dict]:
    entrees = list(brut.values()) if isinstance(brut, dict) else brut
    for e in entrees:
        if not {"date", "enfant", "activite", "creneaux", "statut"} <= e.keys():
            raise ValueError(f"Entrée kidsync invalide ({origine}) : {sorted(e)}")
    return entrees


def charger(chemin: str | Path) -> list[dict]:
    """Les entrées du fichier de kidsync ({clé: entrée} ou liste d'entrées)."""
    return _valider(json.loads(Path(chemin).read_text(encoding="utf-8")), str(chemin))


def lire(cfg: Config) -> list[dict]:
    """Va chercher les inscriptions : sur le serveur de kidsync si KIDSYNC_URL est donné, sinon dans le fichier."""
    if not cfg.url_kidsync:
        return charger(cfg.fichier_kidsync)
    import httpx

    try:
        r = httpx.get(cfg.url_kidsync, headers={"Authorization": f"Bearer {cfg.jeton_kidsync}"}, timeout=15)
    except httpx.TransportError as e:
        raise SourceInjoignable(type(e).__name__) from None
    if r.status_code in (401, 403):
        raise PermissionError("Jeton kidsync refusé")  # une vraie panne de réglage : comptée comme échec
    r.raise_for_status()
    return _valider(r.json(), "KIDSYNC_URL")


def cle(enfant: str) -> str:
    sans_accents = unicodedata.normalize("NFD", enfant).encode("ascii", "ignore").decode()
    return "kidsync-" + "-".join(sans_accents.lower().split())


def enfants(entrees: list[dict]) -> list[str]:
    return sorted({e["enfant"] for e in entrees})


def creneaux(entrees: list[dict], enfant: str, debut: date, fin: date) -> list[Creneau]:
    """Un créneau par jour réservé de cet enfant entre deux dates incluses."""
    par_jour: dict[date, list[dict]] = {}
    for e in entrees:
        jour = date.fromisoformat(e["date"])
        if e["enfant"] == enfant and e["statut"] == "reserve" and debut <= jour <= fin:
            par_jour.setdefault(jour, []).append(e)
    resultat = []
    for jour, es in sorted(par_jour.items()):
        try:
            bornes = [HORAIRES[c] for e in es for c in e["creneaux"]]
        except KeyError as inconnu:
            raise ValueError(f"Créneau MJC inconnu : {inconnu}") from None
        postes = " + ".join(dict.fromkeys(POSTES.get(e["activite"], e["activite"]) for e in es))
        resultat.append(Creneau(
            jour=jour, code=postes, poste=postes, categorie=CATEGORIE,
            debut=min(b[0] for b in bornes), fin=max(b[1] for b in bornes),
        ))
    return resultat


def passage_kidsync(cfg: Config, lus_par_jour: dict[date, Creneau], agenda, aujourdhui: date) -> list[Changement]:
    """Rend l'agenda identique aux inscriptions d'un enfant, d'aujourd'hui à la fin de la fenêtre."""
    debut, fin = fenetre(aujourdhui, cfg.semaines)
    with etape("Google Agenda"):
        connus = agenda.lister(debut, fin)
        if not cfg.dry_run:
            agenda.ajouter_categories()
            agenda.rafraichir_titres()
    changements = comparer(connus, lus_par_jour)
    log.info("%s : %d inscription(s), %d changement(s)", cfg.personne, len(lus_par_jour), len(changements))
    if cfg.dry_run:
        for ch in changements:
            log.info("[dry-run] %s %s %s", cfg.personne, ch.type, ch.jour)
        return changements
    with etape("Google Agenda"):
        agenda.appliquer(changements, aujourdhui, exact=True)
    return changements


def passer_kidsync(cfg: Config, service=None, aujourdhui: date | None = None) -> bool:
    """Un passage pour tous les enfants du fichier. Lève en cas d'erreur (à l'appelant de la compter).

    Rend False quand le serveur de kidsync est injoignable : l'agenda n'est alors pas touché."""
    from .agenda import Agenda

    aujourdhui = aujourdhui or datetime.now(ZoneInfo(cfg.fuseau)).date()
    try:
        entrees = lire(cfg)
    except SourceInjoignable as e:
        # Pas une panne : le poste de kidsync peut être éteint. L'agenda garde ses derniers événements.
        log.warning("Serveur kidsync injoignable (%s) ; agenda inchangé", e)
        return False
    debut, fin = fenetre(aujourdhui, cfg.semaines)
    for enfant in enfants(entrees):
        pcfg = replace(cfg, personne=enfant.capitalize(), cle_synchro=cle(enfant), couleur=None, rappels=())
        agenda = Agenda(pcfg, service=service)
        service = agenda.service  # une seule connexion pour tout le passage
        lus = {c.jour: c for c in creneaux(entrees, enfant, debut, fin)}
        passage_kidsync(pcfg, lus, agenda, aujourdhui)
    return True


def synchro_kidsync(cfg: Config, service=None, aujourdhui: date | None = None) -> int:
    """CONNECTOR=kidsync, seul : un conteneur qui ne fait que les inscriptions. Pas de mail : la santé
    du conteneur (compteur d'échecs) suffit à signaler une panne. Le plus simple est de ne pas en
    faire un conteneur à part : avec CONNECTOR=horaires et KIDSYNC_URL, le passage des horaires
    fait aussi celui-ci (voir horaires.synchro_horaires)."""
    from .alerte import Compteur

    maintenant = datetime.now(ZoneInfo(cfg.fuseau))
    compteur = Compteur(cfg.dossier_etat)
    try:
        passer_kidsync(cfg, service, aujourdhui)
    except Exception as e:
        n = compteur.echec(maintenant)
        # Le type seul : le texte brut d'une erreur peut contenir des données.
        log.error("Passage en échec (%s : %s), %d échec(s) d'affilée",
                  getattr(e, "etape", "Kidsync"), type(e).__name__, n)
        return 1
    compteur.succes()
    return 0
