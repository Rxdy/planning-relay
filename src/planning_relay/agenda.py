"""F4 : Google Agenda, qui sert aussi d'état de référence (F2).

Seuls les événements portant la propriété privée `planning_relay` sont lus ou
modifiés : les saisies manuelles restent intactes.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import Config
from .modeles import Changement, Creneau

PROP = "planning_relay"


def _t(valeur: str) -> time | None:
    return time.fromisoformat(valeur) if valeur else None


def creneau_depuis_proprietes(props: dict[str, str]) -> Creneau:
    return Creneau(
        jour=date.fromisoformat(props["jour"]),
        code=props["code"],
        debut=_t(props.get("debut", "")),
        fin=_t(props.get("fin", "")),
        duree=props.get("duree") or None,
        pause=props.get("pause") or None,
    )


def evenement(c: Creneau, cfg: Config) -> dict:
    props = {
        PROP: cfg.cle_synchro,
        "jour": c.jour.isoformat(),
        "code": c.code,
        "debut": c.debut.isoformat("minutes") if c.debut else "",
        "fin": c.fin.isoformat("minutes") if c.fin else "",
        "duree": c.duree or "",
        "pause": c.pause or "",
    }
    details = [f"Durée : {c.duree}" if c.duree else "", f"Pause : {c.pause}" if c.pause else ""]
    corps = {
        "summary": cfg.titre.format(personne=cfg.personne, code=c.code),
        "description": "\n".join(d for d in details if d) + "\n\nSynchronisé depuis Silae RH Suite.",
        "extendedProperties": {"private": props},
        "reminders": {"useDefault": False},
    }
    if cfg.couleur:
        corps["colorId"] = cfg.couleur
    if c.journee_entiere:
        corps["start"] = {"date": c.jour.isoformat()}
        corps["end"] = {"date": (c.jour + timedelta(days=1)).isoformat()}
    else:
        debut, fin = c.bornes()
        corps["start"] = {"dateTime": debut.isoformat(), "timeZone": cfg.fuseau}
        corps["end"] = {"dateTime": fin.isoformat(), "timeZone": cfg.fuseau}
    return corps


class Agenda:
    def __init__(self, cfg: Config, service=None):
        self.cfg = cfg
        self.service = service or self._service()
        self._ids: dict[date, str] = {}

    def _service(self):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        infos = json.loads(self.cfg.google_sa_json)
        creds = service_account.Credentials.from_service_account_info(
            infos, scopes=["https://www.googleapis.com/auth/calendar.events"]
        )
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    def lister(self, debut: date, fin: date) -> dict[date, Creneau]:
        """Créneaux déjà synchronisés dont le jour est dans [debut, fin]."""
        tz = ZoneInfo(self.cfg.fuseau)
        params = {
            "calendarId": self.cfg.calendar_id,
            "privateExtendedProperty": f"{PROP}={self.cfg.cle_synchro}",
            # Marge d'un jour : une NIGHT de la veille déborde sur aujourd'hui
            "timeMin": _iso_minuit(debut - timedelta(days=1), tz),
            "timeMax": _iso_minuit(fin + timedelta(days=1), tz),
            "singleEvents": True,
            "maxResults": 250,
        }
        connus: dict[date, Creneau] = {}
        self._ids.clear()
        requete = self.service.events().list(**params)
        while requete is not None:
            reponse = requete.execute()
            for ev in reponse.get("items", []):
                c = creneau_depuis_proprietes(ev["extendedProperties"]["private"])
                if not debut <= c.jour <= fin:
                    continue
                if c.jour in connus:
                    # Doublon laissé par une exécution interrompue : on le retire.
                    self._supprimer(ev["id"])
                    continue
                connus[c.jour] = c
                self._ids[c.jour] = ev["id"]
            requete = self.service.events().list_next(requete, reponse)
        return connus

    def appliquer(self, changements: list[Changement]) -> None:
        events = self.service.events()
        for ch in changements:
            if ch.type == "ajout":
                events.insert(calendarId=self.cfg.calendar_id, body=evenement(ch.apres, self.cfg)).execute()
            elif ch.type == "modif":
                events.update(
                    calendarId=self.cfg.calendar_id,
                    eventId=self._ids[ch.jour],
                    body=evenement(ch.apres, self.cfg),
                ).execute()
            else:
                self._supprimer(self._ids[ch.jour])

    def _supprimer(self, event_id: str) -> None:
        self.service.events().delete(calendarId=self.cfg.calendar_id, eventId=event_id).execute()


def _iso_minuit(jour: date, tz: ZoneInfo) -> str:
    return datetime.combine(jour, time(0), tzinfo=tz).isoformat()
