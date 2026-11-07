"""Inscriptions des enfants : calcul des créneaux et recopie exacte dans l'agenda."""

import json
from dataclasses import replace
from datetime import date, time

import pytest

from planning_relay.agenda import PROP
from planning_relay.config import Config
from planning_relay.kidsync import SourceInjoignable, charger, cle, creneaux, lire, synchro_kidsync

from test_agenda import FauxService

CFG = Config.depuis_env({"CONNECTOR": "kidsync", "CALENDAR_ID": "affichage@group", "WEEKS": "8"})
AUJOURDHUI = date(2026, 11, 9)


def e(jour, enfant, activite, creneaux, statut="reserve"):
    return {"date": jour, "enfant": enfant, "activite": activite, "creneaux": creneaux, "statut": statut}


MERC = ["Matin", "Repas", "Après-midi"]
PERI = ["16h30-17h30", "17h30-19h00"]
ENTREES = [
    e("2026-11-25", "lyam", "mercredis", MERC), e("2026-11-25", "noah", "mercredis", MERC),
    e("2026-12-07", "lyam", "periscolaire", PERI), e("2026-12-07", "noah", "periscolaire", PERI),
    e("2026-12-02", "noah", "mercredis", MERC, "ecart"),
]


def fichier(tmp_path, entrees=ENTREES, forme="dict"):
    f = tmp_path / "inscriptions.json"
    f.write_text(json.dumps({f"{x['date']}|{x['enfant']}|{x['activite']}": x for x in entrees} if forme == "dict" else entrees))
    return f


def evenements(service):
    return sorted(service.events().stock.values(), key=lambda ev: (ev["extendedProperties"]["private"][PROP], ev["start"]["dateTime"]))


def test_mercredi_un_creneau_de_7h30_a_19h():
    (c,) = creneaux(ENTREES, "lyam", date(2026, 11, 9), date(2026, 12, 31))[:1]
    assert (c.jour, c.code, c.poste, c.debut, c.fin, c.categorie) == (date(2026, 11, 25), "MJC", "", time(7, 30), time(19, 0), "periscolaire")


def test_periscolaire_de_16h30_a_19h():
    c = creneaux(ENTREES, "noah", date(2026, 12, 1), date(2026, 12, 31))
    assert [(x.jour, x.code, x.debut, x.fin) for x in c] == [(date(2026, 12, 7), "Périscolaire", time(16, 30), time(19, 0))]


def test_un_ecart_n_est_pas_une_inscription():
    jours = [c.jour for c in creneaux(ENTREES, "noah", date(2026, 11, 9), date(2026, 12, 31))]
    assert date(2026, 12, 2) not in jours


def test_un_seul_creneau_reserve_donne_ses_horaires():
    c = creneaux([e("2026-11-25", "noah", "mercredis", ["Matin"])], "noah", AUJOURDHUI, date(2026, 12, 31))
    assert (c[0].debut, c[0].fin) == (time(7, 30), time(12, 0))


def test_deux_activites_le_meme_jour_sont_reunies():
    c = creneaux([e("2026-11-25", "noah", "mercredis", ["Matin"]), e("2026-11-25", "noah", "periscolaire", PERI)],
                 "noah", AUJOURDHUI, date(2026, 12, 31))
    assert len(c) == 1 and c[0].code == "MJC + Périscolaire" and (c[0].debut, c[0].fin) == (time(7, 30), time(19, 0))


def test_creneau_inconnu_est_une_erreur():
    with pytest.raises(ValueError):
        creneaux([e("2026-11-25", "noah", "mercredis", ["Goûter"])], "noah", AUJOURDHUI, date(2026, 12, 31))


def test_entree_invalide(tmp_path):
    f = tmp_path / "x.json"
    f.write_text('[{"date": "2026-11-25"}]')
    with pytest.raises(ValueError):
        charger(f)


def test_cle_sans_accent():
    assert cle("Lyam") == "kidsync-lyam"


def titres_par_cle(service):
    return sorted((ev["extendedProperties"]["private"][PROP], ev["summary"], ev["start"]["dateTime"][:16])
                  for ev in service.events().stock.values())


def test_les_enfants_inscrits_pareil_ont_une_carte_commune(tmp_path):
    service = FauxService()
    cfg = replace(CFG, dossier_etat=str(tmp_path), fichier_kidsync=str(fichier(tmp_path)))
    assert synchro_kidsync(cfg, service=service, aujourdhui=AUJOURDHUI) == 0
    # titre = les prénoms, comme la carte « Garde » ; le libellé « Périscolaire » vient de la catégorie
    assert titres_par_cle(service) == [
        ("kidsync-lyam-noah", "Lyam et Noah", "2026-11-25T07:30"),
        ("kidsync-lyam-noah", "Lyam et Noah", "2026-12-07T16:30"),
    ]
    evs = service.events().stock.values()
    assert {ev["extendedProperties"]["private"]["categorie"] for ev in evs} == {"periscolaire"}
    assert all("colorId" not in ev for ev in evs)


def test_un_enfant_seul_a_sa_propre_carte(tmp_path):
    service = FauxService()
    entrees = [e("2026-11-25", "lyam", "mercredis", MERC), e("2026-11-25", "noah", "mercredis", MERC, "ecart"),
               e("2026-12-07", "noah", "periscolaire", PERI)]
    cfg = replace(CFG, dossier_etat=str(tmp_path), fichier_kidsync=str(fichier(tmp_path, entrees)))
    synchro_kidsync(cfg, service=service, aujourdhui=AUJOURDHUI)
    assert titres_par_cle(service) == [("kidsync-lyam", "Lyam", "2026-11-25T07:30"), ("kidsync-noah", "Noah", "2026-12-07T16:30")]


def test_horaires_differents_le_meme_jour_deux_cartes(tmp_path):
    service = FauxService()
    entrees = [e("2026-12-07", "lyam", "periscolaire", PERI), e("2026-12-07", "noah", "periscolaire", ["17h30-19h00"])]
    cfg = replace(CFG, dossier_etat=str(tmp_path), fichier_kidsync=str(fichier(tmp_path, entrees)))
    synchro_kidsync(cfg, service=service, aujourdhui=AUJOURDHUI)
    assert titres_par_cle(service) == [("kidsync-lyam", "Lyam", "2026-12-07T16:30"), ("kidsync-noah", "Noah", "2026-12-07T17:30")]


def test_trois_enfants_les_noms_sont_liste_en_francais():
    from planning_relay.kidsync import noms
    assert noms(("lyam",)) == "Lyam"
    assert noms(("lyam", "noah")) == "Lyam et Noah"
    assert noms(("lea", "lyam", "noah")) == "Lea, Lyam et Noah"


def test_second_passage_ne_change_rien(tmp_path):
    service = FauxService()
    cfg = replace(CFG, dossier_etat=str(tmp_path), fichier_kidsync=str(fichier(tmp_path)))
    synchro_kidsync(cfg, service=service, aujourdhui=AUJOURDHUI)
    avant = {k: dict(v) for k, v in service.events().stock.items()}
    synchro_kidsync(cfg, service=service, aujourdhui=AUJOURDHUI)
    assert service.events().stock == avant


def test_un_enfant_qui_passe_en_ecart_la_carte_commune_devient_celle_de_l_autre(tmp_path):
    service = FauxService()
    cfg = replace(CFG, dossier_etat=str(tmp_path), fichier_kidsync=str(fichier(tmp_path)))
    synchro_kidsync(cfg, service=service, aujourdhui=AUJOURDHUI)
    retire = [x if not (x["enfant"] == "noah" and x["date"] == "2026-11-25") else {**x, "statut": "ecart"} for x in ENTREES]
    cfg = replace(cfg, fichier_kidsync=str(fichier(tmp_path, retire)))
    synchro_kidsync(cfg, service=service, aujourdhui=AUJOURDHUI)
    assert titres_par_cle(service) == [
        ("kidsync-lyam", "Lyam", "2026-11-25T07:30"),             # Noah n'y est plus : Lyam seul
        ("kidsync-lyam-noah", "Lyam et Noah", "2026-12-07T16:30"),  # le périscolaire reste commun
    ]


def test_migration_les_anciennes_cartes_par_enfant_sont_remplacees(tmp_path):
    """Avant : une carte par enfant (clés kidsync-lyam et kidsync-noah). Après : une carte commune."""
    service = FauxService()
    separes = [e("2026-11-25", "lyam", "mercredis", MERC), e("2026-11-25", "noah", "mercredis", MERC, "ecart")]
    cfg = replace(CFG, dossier_etat=str(tmp_path), fichier_kidsync=str(fichier(tmp_path, separes)))
    synchro_kidsync(cfg, service=service, aujourdhui=AUJOURDHUI)
    ensemble = [e("2026-11-25", "lyam", "mercredis", MERC), e("2026-11-25", "noah", "mercredis", MERC)]
    cfg = replace(cfg, fichier_kidsync=str(fichier(tmp_path, ensemble)))
    synchro_kidsync(cfg, service=service, aujourdhui=AUJOURDHUI)
    assert titres_par_cle(service) == [("kidsync-lyam-noah", "Lyam et Noah", "2026-11-25T07:30")]


def test_le_passage_ne_touche_pas_aux_evenements_d_une_autre_source(tmp_path):
    service = FauxService()
    cfg = replace(CFG, dossier_etat=str(tmp_path), fichier_kidsync=str(fichier(tmp_path)))
    synchro_kidsync(cfg, service=service, aujourdhui=AUJOURDHUI)
    assert all(ev["extendedProperties"]["private"][PROP].startswith("kidsync-") for ev in evenements(service))


def test_synchro_compte_les_echecs(tmp_path):
    cfg = replace(CFG, dossier_etat=str(tmp_path), fichier_kidsync=str(tmp_path / "absent.json"))
    assert synchro_kidsync(cfg, service=FauxService(), aujourdhui=AUJOURDHUI) == 1
    from planning_relay.alerte import Compteur

    assert Compteur(str(tmp_path)).lire() == 1


# --- Récupération par le réseau ----------------------------------------------------

class Reponse:
    def __init__(self, code=200, corps=None):
        self.status_code, self._corps = code, corps

    def json(self):
        return self._corps

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def avec_url(tmp_path, **kw):
    return replace(CFG, dossier_etat=str(tmp_path), url_kidsync="http://poste:8765/inscriptions.json", jeton_kidsync="s3cret", **kw)


def test_lire_par_url_envoie_le_jeton(monkeypatch, tmp_path):
    vu = {}

    def get(url, headers, timeout):
        vu.update(url=url, auth=headers["Authorization"])
        return Reponse(corps={f"{x['date']}|{x['enfant']}": x for x in ENTREES})

    monkeypatch.setattr("httpx.get", get)
    assert len(lire(avec_url(tmp_path))) == len(ENTREES)
    assert vu == {"url": "http://poste:8765/inscriptions.json", "auth": "Bearer s3cret"}


def test_serveur_injoignable_ne_change_rien_et_ne_compte_pas_d_echec(monkeypatch, tmp_path):
    import httpx

    def get(*a, **k):
        raise httpx.ConnectError("hors ligne")

    monkeypatch.setattr("httpx.get", get)
    service = FauxService()
    assert synchro_kidsync(avec_url(tmp_path), service=service, aujourdhui=AUJOURDHUI) == 0
    assert service.events().stock == {}
    from planning_relay.alerte import Compteur

    assert Compteur(str(tmp_path)).lire() == 0


def test_lire_par_url_leve_source_injoignable(monkeypatch, tmp_path):
    import httpx

    monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectTimeout("lent")))
    with pytest.raises(SourceInjoignable):
        lire(avec_url(tmp_path))


def test_jeton_refuse_est_un_echec_compte(monkeypatch, tmp_path):
    monkeypatch.setattr("httpx.get", lambda *a, **k: Reponse(401))
    assert synchro_kidsync(avec_url(tmp_path), service=FauxService(), aujourdhui=AUJOURDHUI) == 1


# --- Dans le conteneur des horaires : un seul passage, un seul compteur de santé -----------------

HORAIRES = Config.depuis_env({"CONNECTOR": "horaires", "CALENDAR_ID": "affichage@group", "WEEKS": "2"})
LUNDI = date(2026, 11, 9)


def cles(service):
    return {e["extendedProperties"]["private"][PROP] for e in service.events().stock.values()}


def test_le_passage_des_horaires_publie_aussi_les_inscriptions(tmp_path):
    from planning_relay.horaires import synchro_horaires

    service = FauxService()
    cfg = replace(HORAIRES, dossier_etat=str(tmp_path), fichier_kidsync=str(fichier(tmp_path)), semaines=8)
    assert synchro_horaires(cfg, service=service, aujourdhui=LUNDI) == 0
    assert {"horaires-rudy", "horaires-lea", "kidsync-lyam-noah"} <= cles(service)


def test_sans_kidsync_le_passage_des_horaires_ne_change_pas(tmp_path):
    from planning_relay.horaires import synchro_horaires

    service = FauxService()
    assert synchro_horaires(replace(HORAIRES, dossier_etat=str(tmp_path)), service=service, aujourdhui=LUNDI) == 0
    assert not any(c.startswith("kidsync-") for c in cles(service))


def test_echec_kidsync_dans_les_horaires_est_compte_et_ne_masque_rien(tmp_path):
    from planning_relay.alerte import Compteur
    from planning_relay.horaires import synchro_horaires

    cfg = replace(HORAIRES, dossier_etat=str(tmp_path), fichier_kidsync=str(tmp_path / "absent.json"))
    assert synchro_horaires(cfg, service=FauxService(), aujourdhui=LUNDI) == 1
    assert synchro_horaires(cfg, service=FauxService(), aujourdhui=LUNDI) == 1
    assert Compteur(str(tmp_path)).lire() == 2   # d'affilée : un succès des horaires ne remet pas à zéro


def test_serveur_kidsync_injoignable_dans_les_horaires_n_est_pas_un_echec(monkeypatch, tmp_path):
    import httpx

    from planning_relay.alerte import Compteur
    from planning_relay.horaires import synchro_horaires

    monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("éteint")))
    service = FauxService()
    cfg = replace(HORAIRES, dossier_etat=str(tmp_path), url_kidsync="http://poste:8765/inscriptions.json", jeton_kidsync="x")
    assert synchro_horaires(cfg, service=service, aujourdhui=LUNDI) == 0
    assert Compteur(str(tmp_path)).lire() == 0
    assert "horaires-rudy" in cles(service)   # les horaires sont publiés quand même
