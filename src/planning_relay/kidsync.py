"""Inscriptions des enfants (CONNECTOR=kidsync) : la MJC, lue dans le fichier de kidsync.

kidsync (~/dev/kidsync) relève le planning du portail MJC et écrit `data/inscriptions.json` :
une entrée par (date, enfant, activité) avec un statut `prevu`, `reserve` ou `ecart`.
Ici, seules les inscriptions `reserve` vont dans l'agenda : un écart ou un jour jamais relevé
n'est pas une inscription. L'agenda est la copie exacte du fichier (comme `horaires`) :
une inscription qui repasse en écart est retirée, et aucun mail n'est envoyé.

Une carte commune par groupe d'enfants inscrits pareil le même jour : « Lyam et Noah », comme la carte
« Garde » (le libellé « Périscolaire » vient de la catégorie, AbView l'affiche au-dessus). Si un seul
est inscrit, ou si leurs horaires diffèrent, chacun a sa carte (« Lyam »). Chaque groupe a sa clé de
synchro (`planning_relay=kidsync-lyam-noah`, `kidsync-lyam`…). Au plus un événement par enfant et par
jour : si deux activités tombent le même jour, elles sont réunies (début le plus tôt, fin la plus tard).

Source : le serveur de kidsync (KIDSYNC_URL + KIDSYNC_TOKEN), que le conteneur interroge à chaque
passage, ou un fichier local (KIDSYNC_FILE). Si le serveur est injoignable, le passage ne change rien
et n'est pas compté comme un échec.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from itertools import combinations
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
# « Repas » existe aux deux portails avec des horaires différents : celui de l'école est à part
HORAIRES_ACTIVITE = {"cantine": {"Repas": (time(11, 30), time(13, 30))}}
POSTES = {"mercredis": "MJC", "periscolaire": "Périscolaire", "cantine": "Cantine"}
# Une famille = un type de carte dans AbView, avec sa catégorie (donc sa couleur) : le périscolaire et les
# mercredis MJC ensemble, la cantine (portail école) à part. Le même jour, un enfant a une carte par famille.
FAMILLES = {"periscolaire": ("mercredis", "periscolaire"), "cantine": ("cantine",)}
CATEGORIES = {"periscolaire": "periscolaire", "cantine": "cantine"}


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
    except (httpx.NetworkError, httpx.TimeoutException) as e:  # pas UnsupportedProtocol : une URL fausse est une panne de réglage
        raise SourceInjoignable(type(e).__name__) from None
    if r.status_code in (401, 403):
        raise PermissionError("Jeton kidsync refusé")  # une vraie panne de réglage : comptée comme échec
    r.raise_for_status()
    return _valider(r.json(), "KIDSYNC_URL")


def _sans_accents(texte: str) -> str:
    return unicodedata.normalize("NFD", texte).encode("ascii", "ignore").decode()


def cle(groupe: str | tuple[str, ...], famille: str = "periscolaire") -> str:
    """« kidsync-lyam », « kidsync-lyam-noah » (ordre alphabétique) ; la cantine ajoute son préfixe :
    « kidsync-cantine-lyam-noah ». Le périscolaire garde sa clé d'origine : les cartes déjà publiées restent."""
    noms_ = [groupe] if isinstance(groupe, str) else sorted(groupe)
    prefixe = "" if famille == "periscolaire" else f"{famille}-"
    return "kidsync-" + prefixe + "-".join("-".join(_sans_accents(n).lower().split()) for n in noms_)


def noms(groupe: tuple[str, ...]) -> str:
    """« Lyam », « Lyam et Noah », « Lea, Lyam et Noah »."""
    n = [x.capitalize() for x in sorted(groupe)]
    return n[0] if len(n) == 1 else ", ".join(n[:-1]) + " et " + n[-1]


def enfants(entrees: list[dict]) -> list[str]:
    return sorted({e["enfant"] for e in entrees})


def _bornes(activite: str, libelle: str) -> tuple[time, time]:
    try:
        return HORAIRES_ACTIVITE.get(activite, {}).get(libelle) or HORAIRES[libelle]
    except KeyError:
        raise ValueError(f"Créneau inconnu : {libelle!r} ({activite})") from None


def creneaux(entrees: list[dict], enfant: str, debut: date, fin: date, famille: str = "periscolaire") -> list[Creneau]:
    """Un créneau par jour réservé de cet enfant, pour un type de carte, entre deux dates incluses."""
    par_jour: dict[date, list[dict]] = {}
    for e in entrees:
        jour = date.fromisoformat(e["date"])
        if (e["enfant"] == enfant and e["statut"] == "reserve" and e["activite"] in FAMILLES[famille]
                and debut <= jour <= fin):
            par_jour.setdefault(jour, []).append(e)
    resultat = []
    for jour, es in sorted(par_jour.items()):
        bornes = [_bornes(e["activite"], c) for e in es for c in e["creneaux"]]
        # dans l'ordre des activités de la famille, pas celui du fichier : le libellé sert de signature de groupe
        ordre = FAMILLES[famille]
        postes = " + ".join(dict.fromkeys(
            POSTES.get(e["activite"], e["activite"]) for e in sorted(es, key=lambda x: ordre.index(x["activite"]))))
        # poste="" : le titre est les prénoms seuls (cfg.personne), comme la carte « Garde »
        resultat.append(Creneau(
            jour=jour, code=postes, poste="", intitule=postes, categorie=CATEGORIES[famille],
            debut=min(b[0] for b in bornes), fin=max(b[1] for b in bornes),
        ))
    return resultat


def creneaux_par_groupe(entrees: list[dict], debut: date, fin: date, suivis: tuple[str, ...] = ()) -> dict[tuple[str, tuple[str, ...]], dict[date, Creneau]]:
    """{(famille, groupe d'enfants): {jour: créneau}}. Le même jour, les enfants au même créneau forment un groupe.

    Tous les groupes possibles sont présents (même vides) : un groupe qui n'a plus aucun jour doit voir
    ses anciens événements retirés de l'agenda (ex. Noah repasse en écart : la carte « Lyam et Noah »
    laisse la place à la carte « Lyam »)."""
    tous = sorted(set(enfants(entrees)) | set(suivis))
    resultat: dict[tuple[str, tuple[str, ...]], dict[date, Creneau]] = {}
    for famille in FAMILLES:
        par_enfant = {e: {c.jour: c for c in creneaux(entrees, e, debut, fin, famille)} for e in tous}
        for r in range(1, len(tous) + 1):
            for g in combinations(tous, r):
                resultat[(famille, g)] = {}
        for jour in sorted({j for cs in par_enfant.values() for j in cs}):
            par_signature: dict[tuple, list[str]] = {}
            for e in tous:
                c = par_enfant[e].get(jour)
                if c:
                    par_signature.setdefault((c.code, c.debut, c.fin), []).append(e)
            for membres in par_signature.values():
                resultat[(famille, tuple(sorted(membres)))][jour] = par_enfant[membres[0]][jour]
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
    """Un passage pour tous les groupes d'enfants du fichier. Lève en cas d'erreur (à l'appelant de la compter).

    Rend False quand le serveur de kidsync est injoignable : l'agenda n'est alors pas touché."""
    from .agenda import Agenda

    aujourdhui = aujourdhui or datetime.now(ZoneInfo(cfg.fuseau)).date()
    try:
        entrees = lire(cfg)
    except SourceInjoignable as e:
        # Pas une panne : le poste de kidsync peut être éteint. L'agenda garde ses derniers événements.
        log.warning("Serveur kidsync injoignable (%s) ; agenda inchangé", e)
        return False
    if not any(e["activite"] in FAMILLES["periscolaire"] for e in entrees):
        # kidsync relève toujours les mercredis et le périscolaire : un fichier qui n'en a aucune ligne est tronqué
        # ou vidé, et le suivre effacerait tous les événements de l'agenda
        raise ValueError("Fichier kidsync sans aucune ligne mercredis/périscolaire : suspect, agenda inchangé")
    debut, fin = fenetre(aujourdhui, cfg.semaines)
    for (famille, groupe), lus in creneaux_par_groupe(entrees, debut, fin, cfg.enfants_kidsync).items():
        pcfg = replace(cfg, personne=noms(groupe), cle_synchro=cle(groupe, famille), couleur=None, rappels=())
        agenda = Agenda(pcfg, service=service)
        service = agenda.service  # une seule connexion pour tout le passage
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
