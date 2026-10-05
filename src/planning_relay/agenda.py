"""F4 : Google Agenda, qui sert aussi d'état de référence (F2).

Seuls les événements portant la propriété privée `planning_relay` sont lus ou
modifiés : les saisies manuelles restent intactes.

Les changements restent visibles sur l'affichage : un créneau modifié porte
« (modifié) » et l'ancien créneau en description ; un créneau supprimé n'est
pas effacé mais grisé avec « (supprimé) ». Ce marqueur ne compte pas comme
créneau connu, et il est réutilisé si un créneau revient ce jour-là.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import Config
from .modeles import Changement, Creneau, poste

PROP = "planning_relay"
MENTIONS = {"modifie": " (modifié)", "supprime": " (supprimé)"}
GRIS = "8"  # Graphite dans la palette Google Agenda


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
        intitule=props.get("intitule") or None,
    )


def _nom(c: Creneau) -> str:
    """Matin, Soir ou Nuit ; pour une absence journée entière, son intitulé Silae."""
    p = poste(c)
    return p if p != "Autre" else (c.intitule or c.code)


def _libelle(c: Creneau | None) -> str:
    if c is None:
        return "Repos"
    return _nom(c) if c.journee_entiere else f"{_nom(c)} {c.debut:%H:%M}–{c.fin:%H:%M}"


def titre(c: Creneau, cfg: Config, etat: str = "") -> str:
    return cfg.titre.format(personne=cfg.personne, poste=_nom(c), code=c.code) + MENTIONS.get(etat, "")


def evenement(c: Creneau, cfg: Config, etat: str = "", avant: Creneau | None = None,
              le: date | None = None) -> dict:
    """etat : "" (normal), "modifie" (avant = l'ancien créneau) ou "supprime"."""
    props = {
        PROP: cfg.cle_synchro,
        "jour": c.jour.isoformat(),
        "code": c.code,
        "debut": c.debut.isoformat("minutes") if c.debut else "",
        "fin": c.fin.isoformat("minutes") if c.fin else "",
        "duree": c.duree or "",
        "pause": c.pause or "",
        "intitule": c.intitule or "",
        "etat": etat,
    }
    quand = f" le {le:%d/%m}" if le else ""
    entete = {
        "modifie": f"Modifié{quand} — avant : {_libelle(avant)}",
        "supprime": f"Supprimé du planning{quand}.",
    }.get(etat, "")
    details = [entete, f"Durée : {c.duree}" if c.duree else "", f"Pause : {c.pause}" if c.pause else ""]
    corps = {
        "summary": titre(c, cfg, etat),
        "description": "\n".join(d for d in details if d) + "\n\nSynchronisé depuis Silae RH Suite.",
        "extendedProperties": {"private": props},
        "reminders": {"useDefault": False},
    }
    if etat == "supprime":
        corps["colorId"] = GRIS
        corps["transparency"] = "transparent"  # n'occupe plus le créneau
    elif cfg.couleur:
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
        self._marqueurs: dict[date, tuple[str, Creneau]] = {}  # créneaux supprimés encore affichés
        self._titres: dict[str, tuple[str, str]] = {}  # id → (titre actuel, titre attendu)

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
        self._marqueurs.clear()
        self._titres.clear()
        requete = self.service.events().list(**params)
        while requete is not None:
            reponse = requete.execute()
            for ev in reponse.get("items", []):
                props = ev["extendedProperties"]["private"]
                c = creneau_depuis_proprietes(props)
                if not debut <= c.jour <= fin:
                    continue
                self._titres[ev["id"]] = (ev.get("summary", ""), titre(c, self.cfg, props.get("etat", "")))
                if props.get("etat") == "supprime":
                    if c.jour in self._marqueurs:
                        self._supprimer(ev["id"])
                    else:
                        self._marqueurs[c.jour] = (ev["id"], c)
                    continue
                if c.jour in connus:
                    # Doublon laissé par une exécution interrompue : on le retire.
                    self._supprimer(ev["id"])
                    continue
                connus[c.jour] = c
                self._ids[c.jour] = ev["id"]
            requete = self.service.events().list_next(requete, reponse)
        return connus

    def appliquer(self, changements: list[Changement], aujourdhui: date | None = None) -> None:
        aujourdhui = aujourdhui or datetime.now(ZoneInfo(self.cfg.fuseau)).date()
        for ch in changements:
            if ch.type == "ajout" and ch.jour in self._marqueurs:
                # Un créneau revient sur un jour supprimé : c'est une modification.
                id_, ancien = self._marqueurs.pop(ch.jour)
                self._mettre_a_jour(id_, evenement(ch.apres, self.cfg, "modifie", ancien, aujourdhui))
            elif ch.type == "ajout":
                self.service.events().insert(calendarId=self.cfg.calendar_id, body=evenement(ch.apres, self.cfg)).execute()
            elif ch.type == "modif":
                self._mettre_a_jour(self._ids[ch.jour], evenement(ch.apres, self.cfg, "modifie", ch.avant, aujourdhui))
            else:
                self._mettre_a_jour(self._ids[ch.jour], evenement(ch.avant, self.cfg, "supprime", le=aujourdhui))

    def rafraichir_titres(self) -> int:
        """Remet au format actuel les titres écrits par une version précédente
        (ex. « Charlène — NIGHT » → « Charlène — Nuit »), sans rien d'autre."""
        a_corriger = {i: attendu for i, (actuel, attendu) in self._titres.items() if actuel != attendu}
        for event_id, attendu in a_corriger.items():
            self.service.events().patch(
                calendarId=self.cfg.calendar_id, eventId=event_id, body={"summary": attendu}
            ).execute()
            self._titres[event_id] = (attendu, attendu)
        return len(a_corriger)

    def _mettre_a_jour(self, event_id: str, corps: dict) -> None:
        self.service.events().update(calendarId=self.cfg.calendar_id, eventId=event_id, body=corps).execute()

    def _supprimer(self, event_id: str) -> None:
        self.service.events().delete(calendarId=self.cfg.calendar_id, eventId=event_id).execute()


def _iso_minuit(jour: date, tz: ZoneInfo) -> str:
    return datetime.combine(jour, time(0), tzinfo=tz).isoformat()
