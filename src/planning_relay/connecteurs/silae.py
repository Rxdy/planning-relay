"""Connecteur Silae RH Suite (sirh.software).

Reproduit les appels de l'appli web : formulaire de connexion Symfony, puis
la requête JSON qui alimente la page « Mon planning », puis déconnexion. Une
seule connexion par passage (robots.txt refuse les robots : rester discret).

`/planning/json/employee/events` renvoie deux sortes d'événements :
- `owner: "employee"` : ceux de la personne connectée, avec son matricule ;
- `owner: "service"` : la ligne d'équipe, qui porte des créneaux de collègues.
Seuls les premiers, au matricule PERSON_MATCH, sortent de ce module.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import httpx

from ..modeles import Creneau
from . import LigneIntrouvable

BASE = "https://sirh.software"
USER_AGENT = "planning-relay/0.1 (usage personnel, 1 requete par heure)"
XHR = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
_CSRF = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class ConnexionRefusee(Exception):
    pass


def creneau_depuis_evenement(ev: dict) -> Creneau:
    debut = datetime.fromisoformat(ev["start"])
    # `end` est coupé à 23:59 pour l'affichage ; `_end` porte la vraie fin (NIGHT : le lendemain).
    fin = datetime.fromisoformat(ev.get("_end") or ev["end"])
    journee = bool(ev.get("isAllDay"))
    pause = ev.get("breakTime") or 0
    duree = ev.get("durationText") or None
    return Creneau(
        jour=debut.date(),
        code=str(ev["code"]).strip().upper(),
        debut=None if journee else debut.time().replace(tzinfo=None),
        fin=None if journee else fin.time().replace(tzinfo=None),
        duree=None if journee or duree == "0h" else duree,
        pause=f"{pause} min" if pause and not journee else None,
        intitule=(ev.get("label") or "").strip() or None,
    )


def creneaux_de(evenements: list[dict], matricule: str) -> list[Creneau]:
    """Ne garde que les événements de la personne suivie ; les autres ne quittent jamais cette fonction."""
    siens = [
        ev for ev in evenements
        if ev.get("owner") == "employee" and str(ev.get("employee")) == matricule
    ]
    return [creneau_depuis_evenement(ev) for ev in siens]


class ConnecteurSilae:
    def __init__(self, identifiant: str, mot_de_passe: str, matricule: str):
        if not matricule.isdigit():
            raise ValueError("PERSON_MATCH doit être le matricule Silae (champ `employee`)")
        self.identifiant = identifiant
        self.mot_de_passe = mot_de_passe
        self.matricule = matricule

    def recuperer(self, debut: date, fin: date) -> list[Creneau]:
        with httpx.Client(
            base_url=BASE,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=30,
        ) as client:
            self._connecter(client)
            try:
                evenements = self._evenements(client, debut, fin)
            finally:
                self._deconnecter(client)

        creneaux = creneaux_de(evenements, self.matricule)
        if not creneaux and any(ev.get("owner") == "employee" for ev in evenements):
            raise LigneIntrouvable("Des événements personnels existent mais aucun pour ce matricule")
        return [c for c in creneaux if debut <= c.jour <= fin]

    def _connecter(self, client: httpx.Client) -> None:
        page = client.get("/login")
        page.raise_for_status()
        jeton = _CSRF.search(page.text)
        if not jeton:
            raise ConnexionRefusee("Jeton CSRF absent de la page de connexion : le site a changé ?")
        r = client.post("/login", data={
            "_username": self.identifiant,
            "_password": self.mot_de_passe,
            "_csrf_token": jeton.group(1),
        })
        r.raise_for_status()
        if r.url.path.startswith("/login"):
            raise ConnexionRefusee("Connexion refusée : identifiants expirés ?")

    def _evenements(self, client: httpx.Client, debut: date, fin: date) -> list[dict]:
        # Lundi de la semaine de début → dimanche de la semaine de fin, comme la vue Semaine.
        lundi = debut - timedelta(days=debut.weekday())
        dimanche = fin + timedelta(days=6 - fin.weekday())
        r = client.get("/planning/json/employee/events", headers=XHR, params={
            "from": lundi.isoformat(), "to": dimanche.isoformat(), "view": "timelineWeek",
        })
        r.raise_for_status()
        if r.url.path.startswith("/login"):
            raise ConnexionRefusee("Session perdue avant la lecture du planning")
        return r.json()

    def _deconnecter(self, client: httpx.Client) -> None:
        try:
            client.get("/logout")
        except httpx.HTTPError:
            pass  # Ne doit jamais faire échouer le passage
