"""F4 : Google Agenda, qui sert aussi d'état de référence (F2).

Seuls les événements portant la propriété privée `planning_relay` sont lus ou
modifiés : les saisies manuelles restent intactes.

Les changements restent visibles sur l'affichage : un créneau modifié porte
« (modifié) » et l'ancien créneau en description. Un créneau qui disparaît de
Silae reste tel quel (voir synchro) ; les créneaux grisés « (supprimé) »
écrits par une version précédente redeviennent des créneaux connus.

Chaque poste porte aussi la propriété privée `categorie` = « travail », que
l'affichage (AbView) lit pour colorer la carte ; une absence (journée entière)
n'en a pas.
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


def categorie(c: Creneau) -> str:
    """Catégorie lue par AbView : un poste est du travail, une absence n'a pas de catégorie."""
    return "" if c.journee_entiere else "travail"


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
        "categorie": categorie(c),
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
        self._supprimes: dict[str, Creneau] = {}  # id → créneau grisé par une version précédente
        self._titres: dict[str, tuple[str, str]] = {}  # id → (titre actuel, titre attendu)
        self._sans_categorie: dict[str, dict[str, str]] = {}  # id → propriétés privées complétées

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
        self._supprimes.clear()
        self._titres.clear()
        self._sans_categorie.clear()
        requete = self.service.events().list(**params)
        while requete is not None:
            reponse = requete.execute()
            for ev in reponse.get("items", []):
                props = ev["extendedProperties"]["private"]
                c = creneau_depuis_proprietes(props)
                if not debut <= c.jour <= fin:
                    continue
                self._titres[ev["id"]] = (ev.get("summary", ""), titre(c, self.cfg, props.get("etat", "")))
                if c.jour in connus:
                    # Doublon laissé par une exécution interrompue : on le retire.
                    self._supprimer(ev["id"])
                    continue
                connus[c.jour] = c
                self._ids[c.jour] = ev["id"]
                if props.get("categorie", None) != categorie(c):
                    self._sans_categorie[ev["id"]] = {**props, "categorie": categorie(c)}
                if props.get("etat") == "supprime":
                    self._supprimes[ev["id"]] = c
            requete = self.service.events().list_next(requete, reponse)
        return connus

    def appliquer(self, changements: list[Changement], aujourdhui: date | None = None) -> None:
        aujourdhui = aujourdhui or datetime.now(ZoneInfo(self.cfg.fuseau)).date()
        for ch in changements:
            if ch.type == "ajout":
                self.service.events().insert(calendarId=self.cfg.calendar_id, body=evenement(ch.apres, self.cfg)).execute()
            elif ch.type == "modif":
                self._mettre_a_jour(self._ids[ch.jour], evenement(ch.apres, self.cfg, "modifie", ch.avant, aujourdhui))
            # Une suppression ne touche pas l'agenda : synchro ne la transmet pas.

    def restaurer_supprimes(self) -> int:
        """Remet en créneau normal ceux qu'une version précédente avait grisés
        « (supprimé) » : une annulation ne compte plus, l'agenda garde le créneau."""
        for event_id, c in self._supprimes.items():
            self._mettre_a_jour(event_id, evenement(c, self.cfg))
            self._titres[event_id] = (titre(c, self.cfg), titre(c, self.cfg))
            self._sans_categorie.pop(event_id, None)  # réécrit en entier, catégorie comprise
        n = len(self._supprimes)
        self._supprimes.clear()
        return n

    def ajouter_categories(self) -> int:
        """Pose la propriété « categorie » sur les créneaux écrits par une version
        précédente, sans rien changer d'autre (ni titre, ni mail)."""
        for event_id, props in self._sans_categorie.items():
            self.service.events().patch(
                calendarId=self.cfg.calendar_id, eventId=event_id,
                body={"extendedProperties": {"private": props}},
            ).execute()
        n = len(self._sans_categorie)
        self._sans_categorie.clear()
        return n

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
