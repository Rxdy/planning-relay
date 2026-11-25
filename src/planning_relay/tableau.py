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
from .horaires import JOURS, TYPES, charger, creneaux, fichier_actif

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


def appliquer_formulaire(regles: list[dict], champs: dict[str, list[str]]) -> list[dict]:
    """Reporte les champs du formulaire (p<i>_…) sur une copie des règles ; ce qui n'est pas
    affichable en formulaire (rotations) n'est pas touché."""
    neuf = copy.deepcopy(regles)
    un = lambda k: (champs.get(k) or [""])[0].strip()  # noqa: E731
    for i, p in enumerate(neuf):
        if p["type"] == "fixed":
            planning = {}
            for jour, libelle in zip(JOURS, LIBELLES):
                debut, fin = un(f"p{i}_{jour}_debut"), un(f"p{i}_{jour}_fin")
                if not debut and not fin:
                    planning[jour] = []
                    continue
                if not (HEURE.match(debut) and HEURE.match(fin)):
                    raise ValueError(f"{p['name']}, {libelle.lower()} : indiquer l'heure de début ET de fin (ou laisser vide pour un repos).")
                planning[jour] = [f"{debut}-{fin}"]
            p["schedule"] = planning
        elif p["type"] == "allday":
            p["days"] = [j for j in JOURS if f"p{i}_jour_{j}" in champs]
            for k, alt in enumerate(p.get("alternating", [])):
                if texte := un(f"p{i}_alt{k}"):
                    alt["type"] = texte
    return neuf


# --- Page ----------------------------------------------------------------------

STYLE = """
:root{--fond:#f6f7f9;--carte:#fff;--texte:#1d2330;--doux:#667085;--bord:#d9dde5;--accent:#2f5bd8;--ok:#0b7a43;--ko:#b42318}
@media(prefers-color-scheme:dark){:root{--fond:#14171e;--carte:#1c212b;--texte:#e8eaf0;--doux:#99a1b3;--bord:#323948;--accent:#7ea2ff;--ok:#4cc38a;--ko:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--fond);color:var(--texte);font:16px/1.45 system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px}h1{font-size:1.4rem;margin:.2em 0}h2{font-size:1.1rem;margin:0 0 .6em}
.carte{background:var(--carte);border:1px solid var(--bord);border-radius:12px;padding:16px;margin:14px 0}
.jour{display:grid;grid-template-columns:6.5em 1fr 1fr;gap:8px;align-items:center;margin:6px 0}
input,textarea,button{font:inherit}input[type=time],input[type=text]{padding:6px;border:1px solid var(--bord);border-radius:8px;background:var(--fond);color:var(--texte);width:100%}
textarea{width:100%;min-height:22em;font-family:ui-monospace,monospace;font-size:.85rem;padding:8px;border:1px solid var(--bord);border-radius:8px;background:var(--fond);color:var(--texte)}
button{padding:9px 16px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
button.sec{background:transparent;color:var(--accent)}.doux{color:var(--doux);font-size:.9rem}
.ok{color:var(--ok);font-weight:600}.ko{color:var(--ko);font-weight:600}label.case{margin-right:12px;white-space:nowrap}
.apercu{font-size:.9rem;color:var(--doux);margin-top:8px}form.ligne{display:inline}
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


def page(regles: list[dict], jeton: str, *, message: str = "", erreur: str = "", modifie: bool = False,
         historique: list[str] | None = None, aujourdhui: date | None = None) -> str:
    aujourdhui = aujourdhui or date.today()
    h = escape
    jt = f'<input type="hidden" name="jeton" value="{h(jeton)}">'
    corps = []
    for i, p in enumerate(regles):
        bloc = [f'<section class="carte"><h2>{h(p["name"])}</h2>']
        if p["type"] == "fixed":
            sched = p.get("schedule", {})
            for jour, libelle in zip(JOURS, LIBELLES):
                creneau = (sched.get(jour) or [""])[0]
                debut, _, fin = creneau.partition("-")
                bloc.append(
                    f'<div class="jour"><span>{libelle}</span>'
                    f'<input type="time" name="p{i}_{jour}_debut" value="{h(debut)}" aria-label="{libelle} début">'
                    f'<input type="time" name="p{i}_{jour}_fin" value="{h(fin)}" aria-label="{libelle} fin"></div>'
                )
            bloc.append('<p class="doux">Les deux champs vides = repos.</p>')
        elif p["type"] == "allday":
            bloc.append("<p>Jours de ramassage :</p><p>" + "".join(
                f'<label class="case"><input type="checkbox" name="p{i}_jour_{j}"{" checked" if j in p.get("days", []) else ""}> {l}</label>'
                for j, l in zip(JOURS, LIBELLES)) + "</p>")
            for k, alt in enumerate(p.get("alternating", [])):
                parite = "semaines paires" if alt["weekMod2"] == 0 else "semaines impaires"
                bloc.append(f'<div class="jour"><span>{parite}</span>'
                            f'<input type="text" name="p{i}_alt{k}" value="{h(str(alt.get("type", "")))}" aria-label="{parite}"></div>')
        else:
            bloc.append('<p class="doux">Rotation de postes : à modifier dans « Avancé » (JSON brut) ci-dessous.</p>')
        bloc.append(f'<div class="apercu">7 prochains jours : {h(_apercu(p, aujourdhui))}</div></section>')
        corps.append("".join(bloc))
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
<section class="carte"><h2>Avancé</h2><details><summary>JSON brut (rotations, exceptions, semaines particulières)</summary>
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
            self._repondre(page(regles, jeton, modifie=modifie, historique=sauvegardes(cfg), **kw))

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
