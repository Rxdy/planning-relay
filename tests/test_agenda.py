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
LE = date(2026, 10, 6)  # jour du passage, écrit dans la description


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

    def patch(self, calendarId, eventId, body):
        self.appels.append(("patch", calendarId, eventId))
        def faire():
            self.stock[eventId] = {**self.stock[eventId], **body}
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
    agenda.appliquer(changements, LE)
    return changements


SEMAINE = {j: c for j, c in SEMAINE_41.items() if j >= DEBUT}


# --- Publication -----------------------------------------------------------

def test_publication_cree_un_evenement_par_jour(agenda):
    chs = synchroniser(agenda, SEMAINE)
    assert [c.type for c in chs] == ["ajout"] * 6
    assert len(stock(agenda)) == 6


def test_evenement_horaire_titre_couleur_description():
    ev = evenement(soir(date(2026, 10, 7)), CFG)
    assert ev["summary"] == "Charlène — Soir"
    assert ev["colorId"] == "6"
    assert ev["start"] == {"dateTime": "2026-10-07T14:45:00", "timeZone": "Europe/Paris"}
    assert ev["end"] == {"dateTime": "2026-10-07T22:45:00", "timeZone": "Europe/Paris"}
    assert ev["description"].startswith("Durée : 7h30\nPause : 30 min\n\nSynchronisé")
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

def evenement_du(agenda, jour):
    [ev] = [e for e in stock(agenda).values() if e["extendedProperties"]["private"]["jour"] == jour]
    return ev


def test_modification_met_a_jour_le_meme_evenement_et_le_marque(agenda):
    synchroniser(agenda, SEMAINE)
    id_mer = evenement_du(agenda, "2026-10-07")["id"]
    nouveau = {**SEMAINE, date(2026, 10, 7): soir(date(2026, 10, 7))}
    chs = synchroniser(agenda, nouveau)
    assert [(c.type, c.jour) for c in chs] == [("modif", date(2026, 10, 7))]
    assert len(stock(agenda)) == 6
    ev = stock(agenda)[id_mer]
    assert ev["summary"] == "Charlène — Soir (modifié)"
    assert ev["description"].startswith("Modifié le 06/10 — avant : Nuit 22:45–07:00\nDurée : 7h30")
    assert ev["end"]["dateTime"] == "2026-10-07T22:45:00"
    assert ev["colorId"] == "6"


def test_creneau_modifie_reste_connu_avec_son_nouveau_contenu(agenda):
    synchroniser(agenda, SEMAINE)
    nouveau = {**SEMAINE, date(2026, 10, 7): soir(date(2026, 10, 7))}
    synchroniser(agenda, nouveau)
    assert synchroniser(agenda, nouveau) == []


def test_repos_devient_horaire(agenda):
    synchroniser(agenda, SEMAINE)
    synchroniser(agenda, {**SEMAINE, date(2026, 10, 8): matin(date(2026, 10, 8))})
    ev = evenement_du(agenda, "2026-10-08")
    assert "date" not in ev["start"] and ev["start"]["dateTime"] == "2026-10-08T07:00:00"


# --- Suppression -----------------------------------------------------------

SANS_MER = {j: c for j, c in SEMAINE.items() if j != date(2026, 10, 7)}


def marquer_supprime(agenda, c, id_):
    """Créneau grisé « (supprimé) » comme l'écrivait une version précédente."""
    stock(agenda)[id_] = {**evenement(c, CFG, "supprime", le=LE), "id": id_}


def test_ancien_creneau_grise_redevient_connu_et_normal(agenda):
    synchroniser(agenda, SANS_MER)
    marquer_supprime(agenda, night(date(2026, 10, 7)), "gris")
    assert agenda.lister(DEBUT, FIN) == SEMAINE
    assert agenda.restaurer_supprimes() == 1
    ev = stock(agenda)["gris"]
    assert ev["summary"] == "Charlène — Nuit" and ev["colorId"] == "6"
    assert "transparency" not in ev and not ev["description"].startswith("Supprimé")
    assert agenda.rafraichir_titres() == 0
    assert synchroniser(agenda, SEMAINE) == []  # republié à l'identique : rien


def test_restaurer_sans_creneau_grise_n_ecrit_rien(agenda):
    synchroniser(agenda, SEMAINE)
    agenda.lister(DEBUT, FIN)
    agenda.service.events().appels.clear()
    assert agenda.restaurer_supprimes() == 0
    assert agenda.service.events().appels == []


def test_double_creneau_grise_retire(agenda):
    c = night(date(2026, 10, 7))
    for id_ in ("a", "b"):
        marquer_supprime(agenda, c, id_)
    agenda.lister(DEBUT, FIN)
    assert len(stock(agenda)) == 1


def test_evenement_manuel_jamais_touche(agenda):
    events = agenda.service.events()
    manuel = {"id": "perso", "summary": "Anniversaire", "start": {"date": "2026-10-07"}, "end": {"date": "2026-10-08"}}
    events.stock["perso"] = manuel
    synchroniser(agenda, SEMAINE)
    synchroniser(agenda, {**SEMAINE, date(2026, 10, 7): soir(date(2026, 10, 7))})
    synchroniser(agenda, {})  # tout disparaît côté plateforme
    assert events.stock["perso"] == manuel
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


# --- Titres écrits par une version précédente ------------------------------

def test_anciens_titres_remis_au_format_poste_sans_toucher_au_reste(agenda):
    synchroniser(agenda, SEMAINE)
    for ev in stock(agenda).values():  # comme écrit par la v1 : code Silae dans le titre
        props = ev["extendedProperties"]["private"]
        ev["summary"] = f"Charlène — {props['code']}" + (" (modifié)" if props["etat"] == "modifie" else "")
    avant = {i: {k: v for k, v in e.items() if k != "summary"} for i, e in stock(agenda).items()}

    agenda.lister(DEBUT, FIN)
    assert agenda.rafraichir_titres() == 6
    titres = sorted(e["summary"] for e in stock(agenda).values())
    # SEMAINE contient aussi R et RF : ce test passe sous le filtre SKIP_CODES
    assert titres == sorted(["Charlène — Nuit", "Charlène — Nuit", "Charlène — Repos",
                             "Charlène — Récup Férié", "Charlène — Repos", "Charlène — Matin"])
    assert {i: {k: v for k, v in e.items() if k != "summary"} for i, e in stock(agenda).items()} == avant
    assert agenda.rafraichir_titres() == 0


def test_titres_a_jour_rien_a_rafraichir(agenda):
    synchroniser(agenda, SEMAINE)
    agenda.lister(DEBUT, FIN)
    assert agenda.rafraichir_titres() == 0
    assert not any(a[0] == "patch" for a in agenda.service.events().appels)
