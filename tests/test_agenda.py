"""F4 contre un faux Google Agenda qui garde les événements en mémoire."""

from datetime import date, time
import itertools

import pytest

from planning_relay.agenda import PROP, Agenda, evenement
from planning_relay.apercu import SEMAINE_41, matin, night, soir
from planning_relay.comparaison import comparer
from planning_relay.config import Config

CFG = Config.depuis_env({"PERSON_NAME": "Charlène", "CALENDAR_ID": "affichage@group", "SYNC_KEY": "charlene",
                         "EVENT_COLOR_ID": "6"})
DEBUT, FIN = date(2026, 10, 6), date(2026, 11, 29)


class _Requete:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FauxEvents:
    """Imite service.events() : list (paginé), list_next, insert, update, delete."""

    def __init__(self, taille_page=3):
        self.stock: dict[str, dict] = {}
        self.ids = (f"ev{i}" for i in itertools.count(1))
        self.appels: list[tuple] = []
        self.taille_page = taille_page

    def list(self, calendarId, privateExtendedProperty, timeMin, timeMax, singleEvents, maxResults, pageToken=None):
        self.appels.append(("list", calendarId, privateExtendedProperty))
        cle, val = privateExtendedProperty.split("=")
        tous = [e for e in self.stock.values()
                if e.get("extendedProperties", {}).get("private", {}).get(cle) == val]
        debut = int(pageToken or 0)
        page = tous[debut:debut + self.taille_page]
        suite = str(debut + self.taille_page) if debut + self.taille_page < len(tous) else None
        r = _Requete(lambda: {"items": page, **({"nextPageToken": suite} if suite else {})})
        r.params = dict(calendarId=calendarId, privateExtendedProperty=privateExtendedProperty, timeMin=timeMin,
                        timeMax=timeMax, singleEvents=singleEvents, maxResults=maxResults)
        return r

    def list_next(self, requete, reponse):
        if "nextPageToken" not in reponse:
            return None
        return self.list(**requete.params, pageToken=reponse["nextPageToken"])

    def insert(self, calendarId, body):
        self.appels.append(("insert", calendarId, body["start"]))
        def faire():
            id_ = next(self.ids)
            self.stock[id_] = {**body, "id": id_}
            return self.stock[id_]
        return _Requete(faire)

    def update(self, calendarId, eventId, body):
        self.appels.append(("update", calendarId, eventId))
        def faire():
            assert eventId in self.stock, "mise à jour d'un événement inconnu"
            self.stock[eventId] = {**body, "id": eventId}
            return self.stock[eventId]
        return _Requete(faire)

    def delete(self, calendarId, eventId):
        self.appels.append(("delete", calendarId, eventId))
        return _Requete(lambda: self.stock.pop(eventId))


class FauxService:
    def __init__(self):
        self._events = FauxEvents()

    def events(self):
        return self._events


@pytest.fixture
def agenda():
    return Agenda(CFG, service=FauxService())


def stock(agenda):
    return agenda.service.events().stock


def synchroniser(agenda, lus):
    """Ce que fait un passage côté agenda : lister, comparer, appliquer."""
    lus = {j: c for j, c in lus.items() if DEBUT <= j <= FIN}
    changements = comparer(agenda.lister(DEBUT, FIN), lus)
    agenda.appliquer(changements)
    return changements


SEMAINE = {j: c for j, c in SEMAINE_41.items() if j >= DEBUT}


# --- Publication -----------------------------------------------------------

def test_publication_cree_un_evenement_par_jour(agenda):
    chs = synchroniser(agenda, SEMAINE)
    assert [c.type for c in chs] == ["ajout"] * 6
    assert len(stock(agenda)) == 6


def test_evenement_horaire_titre_couleur_description():
    ev = evenement(soir(date(2026, 10, 7)), CFG)
    assert ev["summary"] == "Charlène — SOIR"
    assert ev["colorId"] == "6"
    assert ev["start"] == {"dateTime": "2026-10-07T14:45:00", "timeZone": "Europe/Paris"}
    assert ev["end"] == {"dateTime": "2026-10-07T22:45:00", "timeZone": "Europe/Paris"}
    assert ev["description"].startswith("RECEP SOIR\nDurée : 7h30\nPause : 30 min")
    assert ev["reminders"] == {"useDefault": False}


def test_night_finit_le_lendemain_matin():
    ev = evenement(night(date(2026, 10, 7)), CFG)
    assert ev["end"]["dateTime"] == "2026-10-08T07:00:00"


def test_repos_en_journee_entiere():
    from planning_relay.apercu import repos

    ev = evenement(repos(date(2026, 10, 8)), CFG)
    assert ev["start"] == {"date": "2026-10-08"} and ev["end"] == {"date": "2026-10-09"}


def test_relecture_redonne_le_meme_planning(agenda):
    synchroniser(agenda, SEMAINE)
    assert agenda.lister(DEBUT, FIN) == SEMAINE


def test_deuxieme_passage_identique_ne_fait_rien(agenda):
    synchroniser(agenda, SEMAINE)
    agenda.service.events().appels.clear()
    assert synchroniser(agenda, SEMAINE) == []
    assert {a[0] for a in agenda.service.events().appels} == {"list"}  # aucune écriture


# --- Modification ----------------------------------------------------------

def test_modification_met_a_jour_le_meme_evenement(agenda):
    synchroniser(agenda, SEMAINE)
    id_mer = next(i for i, e in stock(agenda).items() if e["extendedProperties"]["private"]["jour"] == "2026-10-07")
    nouveau = {**SEMAINE, date(2026, 10, 7): soir(date(2026, 10, 7))}
    chs = synchroniser(agenda, nouveau)
    assert [(c.type, c.jour) for c in chs] == [("modif", date(2026, 10, 7))]
    assert len(stock(agenda)) == 6
    assert stock(agenda)[id_mer]["summary"] == "Charlène — SOIR"
    assert stock(agenda)[id_mer]["end"]["dateTime"] == "2026-10-07T22:45:00"


def test_repos_devient_horaire(agenda):
    synchroniser(agenda, SEMAINE)
    synchroniser(agenda, {**SEMAINE, date(2026, 10, 8): matin(date(2026, 10, 8))})
    ev = next(e for e in stock(agenda).values() if e["extendedProperties"]["private"]["jour"] == "2026-10-08")
    assert "date" not in ev["start"] and ev["start"]["dateTime"] == "2026-10-08T07:00:00"


# --- Suppression -----------------------------------------------------------

def test_suppression_retire_l_evenement(agenda):
    synchroniser(agenda, SEMAINE)
    sans_mer = {j: c for j, c in SEMAINE.items() if j != date(2026, 10, 7)}
    chs = synchroniser(agenda, sans_mer)
    assert [(c.type, c.jour) for c in chs] == [("suppr", date(2026, 10, 7))]
    assert "2026-10-07" not in {e["extendedProperties"]["private"]["jour"] for e in stock(agenda).values()}


def test_evenement_manuel_jamais_touche(agenda):
    events = agenda.service.events()
    manuel = {"id": "perso", "summary": "Anniversaire", "start": {"date": "2026-10-07"}, "end": {"date": "2026-10-08"}}
    events.stock["perso"] = manuel
    synchroniser(agenda, SEMAINE)
    synchroniser(agenda, {})  # tout disparaît côté plateforme
    assert events.stock == {"perso": manuel}
    assert all(a[2] != "perso" for a in events.appels if a[0] in ("update", "delete"))


def test_autre_personne_suivie_jamais_touchee(agenda):
    autre = evenement(soir(date(2026, 10, 7)), Config.depuis_env({"SYNC_KEY": "autre"}))
    agenda.service.events().stock["autre"] = {**autre, "id": "autre"}
    synchroniser(agenda, {})
    assert "autre" in stock(agenda)


def test_jours_hors_fenetre_ignores(agenda):
    passe = night(date(2026, 10, 5))
    agenda.service.events().stock["vieux"] = {**evenement(passe, CFG), "id": "vieux"}
    assert synchroniser(agenda, SEMAINE)[0].type == "ajout"
    assert "vieux" in stock(agenda)


# --- Robustesse ------------------------------------------------------------

def test_doublon_laisse_par_un_passage_interrompu_est_retire(agenda):
    c = soir(date(2026, 10, 7))
    for id_ in ("a", "b"):
        agenda.service.events().stock[id_] = {**evenement(c, CFG), "id": id_}
    assert agenda.lister(DEBUT, FIN) == {c.jour: c}
    assert len(stock(agenda)) == 1


def test_pagination(agenda):
    synchroniser(agenda, SEMAINE)  # 6 événements, pages de 3
    assert len(agenda.lister(DEBUT, FIN)) == 6
    assert [a[0] for a in agenda.service.events().appels].count("list") >= 3


def test_filtre_sur_la_propriete_privee(agenda):
    agenda.lister(DEBUT, FIN)
    assert agenda.service.events().appels[0] == ("list", "affichage@group", f"{PROP}=charlene")
