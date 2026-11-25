"""Tableau de bord web : modifier les horaires et la poubelle sans toucher au code.

Les modifications sont écrites dans `<STATE_DIR>/horaires.json` (le volume du conteneur), que chaque passage
préfère au fichier livré avec l'image (voir horaires.fichier_actif) : pas de PR ni de promotion, elles sont
prises en compte au passage suivant (heure pile). Chaque enregistrement garde l'ancienne version dans
`<STATE_DIR>/horaires.historique/`, et « Revenir aux horaires de l'image » supprime la version modifiée.

Pensé pour n'être joignable que par Tailscale (aucune exposition publique). Défense en plus :
mot de passe optionnel (TABLEAU_MDP, HTTP Basic), jeton anti-CSRF sur chaque formulaire, tout est
échappé, et le contenu est validé (mêmes calculs que les passages) avant d'être écrit.
Variables : TABLEAU_PORT (8080), TABLEAU_HOTE (0.0.0.0, dans le conteneur), TABLEAU_MDP.
"""

from __future__ import annotations

import base64
import copy
import hmac
import json
import logging
import os
import re
import secrets
import time as horloge
from datetime import date, datetime, timedelta
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from .config import Config
from .horaires import JOURS, TYPES, _position, charger, creneaux, fichier_actif

log = logging.getLogger("planning_relay")

LIBELLES = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
HEURE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SAUVEGARDE = re.compile(r"^horaires-\d{8}-\d{6}\.json$")
TAILLE_MAX = 200_000
HISTORIQUE_MAX = 30


# --- Données -------------------------------------------------------------------

def valider(regles: object) -> list[dict]:
    """Même contrôle que les passages : structure, noms uniques, heures lisibles, un créneau par jour."""
    if not isinstance(regles, list) or not regles:
        raise ValueError("Le fichier doit être une liste de personnes, non vide.")
    noms = set()
    for p in regles:
        if not isinstance(p, dict) or not p.get("name") or p.get("type") not in TYPES:
            raise ValueError(f"Personne invalide : {p.get('name') if isinstance(p, dict) else p!r}.")
        if p["name"] in noms:
            raise ValueError(f"Nom en double : {p['name']}.")
        noms.add(p["name"])
    debut = date.today()
    for p in regles:
        try:
            for c in creneaux(p, debut, debut + timedelta(days=62)):
                if c.debut is not None and c.debut == c.fin:
                    raise ValueError("début et fin identiques")
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as e:
            raise ValueError(f"{p['name']} : horaires illisibles ({type(e).__name__} : {e}).") from None
    return regles


NOM_MAX = 40


def fichier_abview(cfg: Config) -> Path:
    return Path(cfg.dossier_config) / "abview.json"


def lire_abview(cfg: Config) -> dict:
    """Les réglages d'AbView modifiés ici ({} : ceux livrés avec AbView). Un fichier abîmé ne casse pas la page."""
    try:
        donnees = json.loads(fichier_abview(cfg).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return donnees if isinstance(donnees, dict) else {}


def enregistrer_abview(cfg: Config, nom: str) -> None:
    """Le nom affiché dans l'en-tête d'AbView. Vide : on revient à celui livré avec AbView."""
    nom = " ".join(nom.split())
    if len(nom) > NOM_MAX or any(not c.isprintable() for c in nom):
        raise ValueError(f"Le nom doit tenir en {NOM_MAX} caractères, sans caractère spécial.")
    cible = fichier_abview(cfg)
    donnees = {k: v for k, v in lire_abview(cfg).items() if k != "name"}
    if nom:
        donnees["name"] = nom
    if not donnees:
        cible.unlink(missing_ok=True)
        return
    cible.parent.mkdir(parents=True, exist_ok=True)
    tmp = cible.with_suffix(".tmp")
    tmp.write_text(json.dumps(donnees, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, cible)
    log.info("Réglages AbView modifiés depuis le tableau de bord")


def lire(cfg: Config) -> list[dict]:
    return charger(fichier_actif(cfg))


def _dossier_historique(cfg: Config) -> Path:
    return Path(cfg.dossier_config) / "horaires.historique"


def enregistrer(cfg: Config, regles: object, maintenant: datetime | None = None) -> None:
    valider(regles)
    cible = Path(cfg.dossier_config) / "horaires.json"
    cible.parent.mkdir(parents=True, exist_ok=True)
    if cible.is_file():
        _sauvegarder(cfg, cible, maintenant)
    tmp = cible.with_suffix(".tmp")
    tmp.write_text(json.dumps(regles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, cible)
    log.info("Horaires modifiés depuis le tableau de bord (%d personne(s))", len(regles))


def _sauvegarder(cfg: Config, source: Path, maintenant: datetime | None) -> None:
    dossier = _dossier_historique(cfg)
    dossier.mkdir(parents=True, exist_ok=True)
    maintenant = maintenant or datetime.now(ZoneInfo(cfg.fuseau))
    (dossier / f"horaires-{maintenant:%Y%m%d-%H%M%S}.json").write_bytes(source.read_bytes())
    for vieux in sorted(dossier.glob("horaires-*.json"))[:-HISTORIQUE_MAX]:
        vieux.unlink()


def sauvegardes(cfg: Config) -> list[str]:
    dossier = _dossier_historique(cfg)
    return sorted((f.name for f in dossier.glob("horaires-*.json")), reverse=True) if dossier.is_dir() else []


def restaurer(cfg: Config, nom: str) -> None:
    if not SAUVEGARDE.match(nom):
        raise ValueError("Sauvegarde inconnue.")
    source = _dossier_historique(cfg) / nom
    if not source.is_file():
        raise ValueError("Sauvegarde introuvable.")
    enregistrer(cfg, json.loads(source.read_text(encoding="utf-8")))


def revenir_a_l_image(cfg: Config) -> None:
    cible = Path(cfg.dossier_config) / "horaires.json"
    if cible.is_file():
        _sauvegarder(cfg, cible, None)
        cible.unlink()


GROUPES = 4          # lignes « jours + début + fin » proposées pour des horaires fixes
SEMAINES_LIBRES = 2  # semaines vides proposées en plus du cycle, pour l'allonger


# --- Conversions entre le JSON (format AbView) et le formulaire -------------------

def _jours_semaine(courant: dict) -> list[str]:
    """Un « HH:MM-HH:MM » (ou "") par jour, exceptions appliquées, d'une semaine de rotation."""
    exc = courant.get("exceptions") or {}
    return [
        ((exc[j] or [""])[0] if exc.get(j) is not None else (courant.get("hours") or [""])[0])
        for j in JOURS
    ]


def _depuis_jours(valeurs: list[str]) -> tuple[list[str], dict]:
    """Inverse : les heures les plus fréquentes deviennent `hours`, les autres jours des `exceptions`."""
    non_vides = [v for v in valeurs if v]
    principal = max(set(non_vides), key=lambda v: (non_vides.count(v), -valeurs.index(v))) if non_vides else ""
    exceptions = {j: ([v] if v else []) for j, v in zip(JOURS, valeurs) if v != principal}
    return ([principal] if principal else []), exceptions


def _groupes(planning: dict) -> list[tuple[str, str, list[str]]]:
    """Jours qui partagent les mêmes heures : [(début, fin, [jours])]."""
    par: dict[str, list[str]] = {}
    for j in JOURS:
        if c := (planning.get(j) or [""])[0]:
            par.setdefault(c, []).append(j)
    return [(c.partition("-")[0], c.partition("-")[2], jours) for c, jours in par.items()]


def _heures(libelle: str, debut: str, fin: str) -> str:
    if not debut and not fin:
        return ""
    if not (HEURE.match(debut) and HEURE.match(fin)):
        raise ValueError(f"{libelle} : indiquer l'heure de début ET de fin (ou laisser vide pour un repos).")
    return f"{debut}-{fin}"


def _rappels_texte(p: dict) -> str:
    return ", ".join(f"{m / 60:g}" for m in p.get("rappels") or [])


def _appliquer_rappels(p: dict, i: int, un) -> None:
    """« 4, 2 » (heures avant le début) → [240, 120] minutes ; vide : aucun rappel."""
    texte = un(f"p{i}_rappels")
    if not texte and "rappels" not in p:
        return
    try:
        minutes = sorted({round(float(x) * 60) for x in texte.replace(";", ",").split(",") if x.strip()}, reverse=True)
    except ValueError:
        raise ValueError(f"{p['name']} : rappels illisibles (« 4, 2 » = 4 h et 2 h avant ; point pour les décimales).") from None
    if any(m < 0 or m > 40320 for m in minutes):
        raise ValueError(f"{p['name']} : un rappel va de 0 à 672 heures avant.")
    p["rappels"] = minutes


def _position_affichee(p: dict, aujourdhui: date) -> int:
    n = len(p.get("rotation") or [])
    return _position(p, aujourdhui, aujourdhui.isocalendar().week) + 1 if n else 1


def appliquer_formulaire(regles: list[dict], champs: dict[str, list[str]]) -> list[dict]:
    """Reporte le formulaire sur une copie des règles (champs p<i>_…) : modifie les personnes,
    retire celles cochées « supprimer », ajoute la nouvelle personne si elle a un nom."""
    un = lambda k: (champs.get(k) or [""])[0].strip()  # noqa: E731
    neuf = []
    for i, p in enumerate(regles):
        if f"p{i}_suppr" in champs:
            continue
        p = copy.deepcopy(p)
        _appliquer_personne(p, i, un, champs)
        neuf.append(p)
    i = len(regles)
    if nom := un(f"p{i}_nom"):
        p = {"name": nom, "type": un(f"p{i}_type") if un(f"p{i}_type") in {"fixed", "shift"} else "fixed"}
        _appliquer_personne(p, i, un, champs)
        neuf.append(p)
    return neuf


def _appliquer_personne(p: dict, i: int, un, champs: dict) -> None:
    nom = p["name"]
    if p["type"] == "allday":
        jour = un(f"p{i}_jour")
        if jour not in JOURS:
            raise ValueError(f"{nom} : choisir un jour.")
        p["days"] = [jour]
        creneau = _heures(f"{nom}, heure", un(f"p{i}_debut"), un(f"p{i}_fin"))
        if creneau:
            p["hours"] = [creneau]
        else:
            p.pop("hours", None)  # journée entière
        for k, alt in enumerate(p.get("alternating", [])):
            if texte := un(f"p{i}_alt{k}"):
                alt["type"] = texte
        _appliquer_rappels(p, i, un)
        return
    if un(f"p{i}_type") in {"fixed", "shift"}:
        p["type"] = un(f"p{i}_type")
    if p["type"] == "fixed":
        planning: dict[str, list[str]] = {j: [] for j in JOURS}
        for k in range(int(un(f"p{i}_ng") or GROUPES)):
            jours = [j for j in JOURS if f"p{i}_g{k}_{j}" in champs]
            creneau = _heures(f"{nom}, ligne {k + 1}", un(f"p{i}_g{k}_debut"), un(f"p{i}_g{k}_fin"))
            if not jours and not creneau:
                continue
            if not jours or not creneau:
                raise ValueError(f"{nom}, ligne {k + 1} : cocher des jours ET indiquer début et fin.")
            for j in jours:
                if planning[j]:
                    raise ValueError(f"{nom} : {LIBELLES[JOURS.index(j)].lower()} est dans deux lignes.")
                planning[j] = [creneau]
        p["schedule"] = planning
        _appliquer_rappels(p, i, un)
        return
    # Postes en rotation
    lignes: list[dict | None] = []
    for w in range(int(un(f"p{i}_ns") or 0)):
        poste = un(f"p{i}_s{w}_poste")
        valeurs = [_heures(f"{nom}, semaine {w + 1}, {l.lower()}", un(f"p{i}_s{w}_{j}_debut"), un(f"p{i}_s{w}_{j}_fin"))
                   for j, l in zip(JOURS, LIBELLES)]
        if not poste and not any(valeurs):
            lignes.append(None)
            continue
        if not poste:
            raise ValueError(f"{nom}, semaine {w + 1} : indiquer le nom du poste (Matin, Soir, Nuit…).")
        hours, exceptions = _depuis_jours(valeurs)
        lignes.append({"week": w + 1, "shift": poste, "hours": hours, "exceptions": exceptions})
    while lignes and lignes[-1] is None:
        lignes.pop()
    if not lignes:
        raise ValueError(f"{nom} : au moins une semaine dans le cycle.")
    if None in lignes:
        raise ValueError(f"{nom} : une semaine vide au milieu du cycle ; remplir ou décaler les suivantes.")
    p["rotation"] = [dict(ligne, week=n + 1) for n, ligne in enumerate(lignes)]
    _appliquer_rappels(p, i, un)
    if "cycleLength" in p:
        p["cycleLength"] = len(lignes)
    try:
        position = int(un(f"p{i}_pos") or 1)
        origine = int(un(f"p{i}_pos0") or 1)
        annee, semaine = (int(x) for x in un(f"p{i}_ancre").split("-"))
    except ValueError:
        raise ValueError(f"{nom} : position dans le cycle illisible.") from None
    if not 1 <= position <= len(lignes):
        raise ValueError(f"{nom} : la position dans le cycle va de 1 à {len(lignes)}.")
    nouveau = "cycleAnchor" not in p and "offset" not in p
    if position != origine or nouveau:
        p["cycleAnchor"] = {"year": annee, "week": semaine, "position": position - 1}
        p.pop("offset", None)


# --- Page ----------------------------------------------------------------------

STYLE = """
:root{--fond:#f6f7f9;--carte:#fff;--texte:#1d2330;--doux:#667085;--bord:#d9dde5;--accent:#2f5bd8;--ok:#0b7a43;--ko:#b42318}
@media(prefers-color-scheme:dark){:root{--fond:#14171e;--carte:#1c212b;--texte:#e8eaf0;--doux:#99a1b3;--bord:#323948;--accent:#7ea2ff;--ok:#4cc38a;--ko:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--fond);color:var(--texte);font:16px/1.45 system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px}h1{font-size:1.4rem;margin:.2em 0}h2{font-size:1.1rem;margin:0 0 .6em}h3{font-size:1rem;margin:.9em 0 .3em}
.carte{background:var(--carte);border:1px solid var(--bord);border-radius:12px;padding:16px;margin:14px 0}
.jour{display:grid;grid-template-columns:6.5em 1fr 1fr;gap:8px;align-items:center;margin:6px 0}
.ligne-fixe{border:1px solid var(--bord);border-radius:10px;padding:10px;margin:8px 0}
input,textarea,button{font:inherit}input[type=time],input[type=text],input[type=number]{padding:6px;border:1px solid var(--bord);border-radius:8px;background:var(--fond);color:var(--texte);width:100%}
input[type=number]{width:5em}.heures{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}
textarea{width:100%;min-height:22em;font-family:ui-monospace,monospace;font-size:.85rem;padding:8px;border:1px solid var(--bord);border-radius:8px;background:var(--fond);color:var(--texte)}
button{padding:9px 16px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
button.sec{background:transparent;color:var(--accent)}.doux{color:var(--doux);font-size:.9rem}
.ok{color:var(--ok);font-weight:600}.ko{color:var(--ko);font-weight:600}label.case{margin-right:12px;white-space:nowrap}
.apercu{font-size:.9rem;color:var(--doux);margin-top:8px}form.ligne{display:inline}
details.semaine{border:1px solid var(--bord);border-radius:10px;padding:8px 10px;margin:6px 0}summary{cursor:pointer}
.danger{margin-top:12px;font-size:.9rem;color:var(--doux)}
@media(max-width:480px){.jour{grid-template-columns:5em 1fr 1fr}}
"""


def _apercu(p: dict, aujourdhui: date) -> str:
    try:
        cs = creneaux(p, aujourdhui, aujourdhui + timedelta(days=6))
    except Exception:  # noqa: BLE001 — l'aperçu ne doit jamais empêcher d'afficher la page
        return "aperçu indisponible"
    if not cs:
        return "rien les 7 prochains jours"
    return " · ".join(
        f"{LIBELLES[c.jour.weekday()][:3].lower()} {c.debut:%H:%M}-{c.fin:%H:%M}" if c.debut
        else f"{LIBELLES[c.jour.weekday()][:3].lower()} {c.poste or c.code}"
        for c in cs
    )


def _section_fixe(i: int, p: dict) -> str:
    h = escape
    groupes = _groupes(p.get("schedule") or {})
    n = max(GROUPES, len(groupes) + 1)
    lignes = [f'<input type="hidden" name="p{i}_ng" value="{n}">']
    for k in range(n):
        debut, fin, jours = groupes[k] if k < len(groupes) else ("", "", [])
        cases = "".join(
            f'<label class="case"><input type="checkbox" name="p{i}_g{k}_{j}"{" checked" if j in jours else ""}> {l[:3]}</label>'
            for j, l in zip(JOURS, LIBELLES))
        lignes.append(
            f'<div class="ligne-fixe"><div>{cases}</div><div class="heures">'
            f'<input type="time" name="p{i}_g{k}_debut" value="{h(debut)}" aria-label="Début, ligne {k + 1}">'
            f'<input type="time" name="p{i}_g{k}_fin" value="{h(fin)}" aria-label="Fin, ligne {k + 1}"></div></div>')
    return ('<p class="doux">Une ligne = des jours + une heure de début et de fin. '
            'Les jours sans ligne sont des repos. Deux lignes si les heures changent un jour (ex. le mercredi).</p>'
            + "".join(lignes))


def _section_postes(i: int, p: dict, aujourdhui: date) -> str:
    h = escape
    rotation = p.get("rotation") or []
    n = len(rotation) + SEMAINES_LIBRES
    iso = aujourdhui.isocalendar()
    pos = _position_affichee(p, aujourdhui)
    out = [
        f'<input type="hidden" name="p{i}_ns" value="{n}">'
        f'<input type="hidden" name="p{i}_ancre" value="{aujourdhui.year}-{iso.week}">'
        f'<input type="hidden" name="p{i}_pos0" value="{pos}">'
        f'<p>Cette semaine (n° {iso.week}) est la semaine n° '
        f'<input type="number" min="1" max="{max(len(rotation), 1)}" name="p{i}_pos" value="{pos}" aria-label="Position dans le cycle"> '
        f'du cycle de {len(rotation) or "…"} semaine(s).</p>'
        '<p class="doux">Le cycle se répète dans l\'ordre ci-dessous. Chaque semaine : le poste, puis les heures de chaque jour '
        '(vide = repos). Les semaines vides en fin de liste servent à allonger le cycle.</p>']
    for w in range(n):
        courant = rotation[w] if w < len(rotation) else {}
        valeurs = _jours_semaine(courant) if courant else [""] * 7
        poste = courant.get("shift", "")
        resume = poste or "(vide)"
        jours = []
        for j, l, v in zip(JOURS, LIBELLES, valeurs):
            d, _, f = v.partition("-")
            jours.append(
                f'<div class="jour"><span>{l}</span>'
                f'<input type="time" name="p{i}_s{w}_{j}_debut" value="{h(d)}" aria-label="{l} début">'
                f'<input type="time" name="p{i}_s{w}_{j}_fin" value="{h(f)}" aria-label="{l} fin"></div>')
        out.append(
            f'<details class="semaine"><summary>Semaine {w + 1} du cycle — {h(resume)}</summary>'
            f'<p>Poste : <input type="text" name="p{i}_s{w}_poste" value="{h(poste)}" placeholder="Matin, Soir, Nuit…"></p>'
            + "".join(jours) + "</details>")
    return "".join(out)


def _carte(i: int, p: dict, aujourdhui: date) -> str:
    h = escape
    bloc = [f'<section class="carte"><h2>{h(p["name"])}</h2>']
    if p["type"] == "allday":
        jour = (p.get("days") or [""])[0]
        bloc.append("<p>Jour de ramassage :</p><p>" + "".join(
            f'<label class="case"><input type="radio" name="p{i}_jour" value="{j}"{" checked" if j == jour else ""}> {l}</label>'
            for j, l in zip(JOURS, LIBELLES)) + "</p>")
        debut, _, fin = (p.get("hours") or [""])[0].partition("-")
        bloc.append('<p>Heure de l\'événement :</p><div class="heures">'
                    f'<input type="time" name="p{i}_debut" value="{h(debut)}" aria-label="Début">'
                    f'<input type="time" name="p{i}_fin" value="{h(fin)}" aria-label="Fin"></div>'
                    '<p class="doux">Vide = journée entière. La couleur alterne chaque semaine :</p>')
        for k, alt in enumerate(p.get("alternating", [])):
            parite = "Semaines paires" if alt["weekMod2"] == 0 else "Semaines impaires"
            bloc.append(f'<div class="jour"><span>{parite}</span>'
                        f'<input type="text" name="p{i}_alt{k}" value="{h(str(alt.get("type", "")))}" aria-label="{parite}"></div>')
    else:
        shift = p["type"] == "shift"
        radio = lambda v, t, coche: (f'<label class="case"><input type="radio" name="p{i}_type" value="{v}"'  # noqa: E731
                                     f'{" checked" if coche else ""}> {t}</label>')
        bloc.append("<p>" + radio("fixed", "Horaires fixes (pas de postes)", not shift)
                    + radio("shift", "Postes en rotation", shift) + "</p>")
        bloc.append(f'<details{"" if shift else " open"}><summary><strong>Horaires fixes</strong></summary>{_section_fixe(i, p)}</details>')
        bloc.append(f'<details{" open" if shift else ""}><summary><strong>Postes en rotation</strong></summary>{_section_postes(i, p, aujourdhui)}</details>')
        bloc.append('<p class="doux">Seule la partie choisie ci-dessus est utilisée.</p>')
    bloc.append(f'<p>Rappels, en heures avant le début : <input type="text" name="p{i}_rappels" value="{h(_rappels_texte(p))}" '
                'placeholder="ex. 4, 2" aria-label="Rappels"></p>')
    bloc.append(f'<div class="apercu">7 prochains jours : {h(_apercu(p, aujourdhui))}</div>')
    bloc.append(f'<label class="danger"><input type="checkbox" name="p{i}_suppr"> Supprimer {h(p["name"])} '
                '(ses événements disparaissent de l\'agenda au prochain passage)</label></section>')
    return "".join(bloc)


def page(regles: list[dict], jeton: str, *, message: str = "", erreur: str = "", modifie: bool = False,
         historique: list[str] | None = None, aujourdhui: date | None = None, abview: dict | None = None) -> str:
    aujourdhui = aujourdhui or date.today()
    h = escape
    jt = f'<input type="hidden" name="jeton" value="{h(jeton)}">'
    corps = [_carte(i, p, aujourdhui) for i, p in enumerate(regles)]
    i = len(regles)
    corps.append(
        f'<section class="carte"><h2>Ajouter une personne</h2><p>Nom : <input type="text" name="p{i}_nom" placeholder="laisser vide pour ne rien ajouter"></p>'
        f'<p><label class="case"><input type="radio" name="p{i}_type" value="fixed" checked> Horaires fixes</label>'
        f'<label class="case"><input type="radio" name="p{i}_type" value="shift"> Postes en rotation</label></p>'
        f'<details><summary><strong>Horaires fixes</strong></summary>{_section_fixe(i, {})}</details>'
        f'<details><summary><strong>Postes en rotation</strong></summary>{_section_postes(i, {}, aujourdhui)}</details></section>')
    nom = escape(str((abview or {}).get("name", "")))
    bloc_abview = (
        f'<section class="carte"><h2>AbView</h2><form method="post" action="/abview">{jt}'
        f'<p>Nom affiché dans l\'en-tête : <input type="text" name="nom" value="{nom}" maxlength="{NOM_MAX}" '
        'placeholder="vide = le nom livré avec AbView" aria-label="Nom dans l\'en-tête"></p>'
        '<p class="doux">Pris en compte quand AbView recharge sa configuration (au rechargement de la page de l\'écran).</p>'
        '<button>Enregistrer le nom</button></form></section>')
    etat = "Horaires modifiés depuis ce tableau de bord." if modifie else "Horaires livrés avec l'image (aucune modification)."
    notif = (f'<p class="ok" role="status">{h(message)}</p>' if message else "") + (
        f'<p class="ko" role="alert">{h(erreur)}</p>' if erreur else "")
    hist = "".join(
        f'<form class="ligne" method="post" action="/restaurer">{jt}<input type="hidden" name="nom" value="{h(n)}">'
        f'<button class="sec">{h(n[9:13])}-{h(n[13:15])}-{h(n[15:17])} {h(n[18:20])}:{h(n[20:22])}</button></form> '
        for n in (historique or [])[:8])
    brut = h(json.dumps(regles, ensure_ascii=False, indent=2))
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Horaires — tableau de bord</title><style>{STYLE}</style></head><body><main>
<h1>Horaires et poubelle</h1><p class="doux">{h(etat)} Pris en compte au prochain passage (à l'heure pile).</p>{notif}
<form method="post" action="/enregistrer">{jt}{"".join(corps)}<button>Enregistrer</button></form>
{bloc_abview}
<section class="carte"><h2>Avancé</h2><details><summary>JSON brut (semaines particulières, couleurs, rappels)</summary>
<form method="post" action="/brut">{jt}<textarea name="json" spellcheck="false">{brut}</textarea><p><button>Enregistrer le JSON</button></p></form></details>
{"<p class='doux'>Versions précédentes :</p>" + hist if hist else ""}
{f'<form method="post" action="/image">{jt}<p><button class="sec">Revenir aux horaires de l&#39;image</button></p></form>' if modifie else ""}
</section></main></body></html>"""


# --- Serveur -------------------------------------------------------------------

def _handler(cfg: Config, mdp: str, jeton: str):
    class Tableau(BaseHTTPRequestHandler):
        server_version = "tableau"

        def log_message(self, *args):  # pas de journal par requête
            pass

        def _autorise(self) -> bool:
            if not mdp:
                return True
            en_tete = self.headers.get("Authorization", "")
            if en_tete.startswith("Basic "):
                try:
                    _, _, donne = base64.b64decode(en_tete[6:]).decode().partition(":")
                except Exception:  # noqa: BLE001
                    donne = ""
                if hmac.compare_digest(donne.encode(), mdp.encode()):
                    return True
            horloge.sleep(1)  # freine les essais répétés
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Horaires"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def _repondre(self, html: str, statut: int = 200) -> None:
            donnees = html.encode()
            self.send_response(statut)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(donnees)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
            self.end_headers()
            self.wfile.write(donnees)

        def _afficher(self, **kw) -> None:
            regles = kw.pop("regles", None) or lire(cfg)
            modifie = (Path(cfg.dossier_config) / "horaires.json").is_file()
            self._repondre(page(regles, jeton, modifie=modifie, historique=sauvegardes(cfg), abview=lire_abview(cfg), **kw))

        def do_GET(self):
            if not self._autorise():
                return
            if self.path.split("?")[0] != "/":
                return self._repondre("<p>Introuvable.</p>", 404)
            self._afficher()

        def do_POST(self):
            if not self._autorise():
                return
            taille = int(self.headers.get("Content-Length") or 0)
            if taille > TAILLE_MAX:
                return self._repondre("<p>Trop gros.</p>", 413)
            champs = parse_qs(self.rfile.read(taille).decode("utf-8", "replace"), keep_blank_values=True)
            if not hmac.compare_digest((champs.get("jeton") or [""])[0].encode(), jeton.encode()):
                return self._repondre("<p>Formulaire expiré : recharger la page.</p>", 403)
            try:
                if self.path == "/enregistrer":
                    enregistrer(cfg, appliquer_formulaire(lire(cfg), champs))
                    return self._afficher(message="Enregistré.")
                if self.path == "/brut":
                    try:
                        regles = json.loads((champs.get("json") or [""])[0])
                    except json.JSONDecodeError as e:
                        raise ValueError(f"JSON invalide : {e}") from None
                    enregistrer(cfg, regles)
                    return self._afficher(message="Enregistré.")
                if self.path == "/abview":
                    enregistrer_abview(cfg, (champs.get("nom") or [""])[0])
                    return self._afficher(message="Nom enregistré.")
                if self.path == "/restaurer":
                    restaurer(cfg, (champs.get("nom") or [""])[0])
                    return self._afficher(message="Version restaurée.")
                if self.path == "/image":
                    revenir_a_l_image(cfg)
                    return self._afficher(message="Retour aux horaires de l'image.")
                return self._repondre("<p>Introuvable.</p>", 404)
            except ValueError as e:
                # Le formulaire est réaffiché avec les données en vigueur, pas celles refusées
                self._afficher(erreur=str(e))
            except Exception as e:  # noqa: BLE001
                log.error("Tableau : erreur inattendue (%s)", type(e).__name__)
                self._afficher(erreur="Erreur inattendue ; rien n'a été modifié.")

    return Tableau


def lancer_tableau(cfg: Config) -> int:
    port = int(os.environ.get("TABLEAU_PORT", "8080"))
    hote = os.environ.get("TABLEAU_HOTE", "0.0.0.0")
    mdp = os.environ.get("TABLEAU_MDP", "")
    serveur = ThreadingHTTPServer((hote, port), _handler(cfg, mdp, secrets.token_urlsafe(24)))
    log.info("Tableau de bord sur %s:%d (%s)", hote, port, "mot de passe" if mdp else "sans mot de passe")
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
