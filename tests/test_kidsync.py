"""Inscriptions des enfants : calcul des créneaux et recopie exacte dans l'agenda."""

import json
from dataclasses import replace
from datetime import date, time

import pytest

from planning_relay.agenda import PROP
from planning_relay.config import Config
from planning_relay.kidsync import charger, cle, creneaux, synchro_kidsync

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
    assert (c.jour, c.poste, c.debut, c.fin, c.categorie) == (date(2026, 11, 25), "MJC", time(7, 30), time(19, 0), "enfants")


def test_periscolaire_de_16h30_a_19h():
    c = creneaux(ENTREES, "noah", date(2026, 12, 1), date(2026, 12, 31))
    assert [(x.jour, x.poste, x.debut, x.fin) for x in c] == [(date(2026, 12, 7), "Périscolaire", time(16, 30), time(19, 0))]


def test_un_ecart_n_est_pas_une_inscription():
    jours = [c.jour for c in creneaux(ENTREES, "noah", date(2026, 11, 9), date(2026, 12, 31))]
    assert date(2026, 12, 2) not in jours


def test_un_seul_creneau_reserve_donne_ses_horaires():
    c = creneaux([e("2026-11-25", "noah", "mercredis", ["Matin"])], "noah", AUJOURDHUI, date(2026, 12, 31))
    assert (c[0].debut, c[0].fin) == (time(7, 30), time(12, 0))


def test_deux_activites_le_meme_jour_sont_reunies():
    c = creneaux([e("2026-11-25", "noah", "mercredis", ["Matin"]), e("2026-11-25", "noah", "periscolaire", PERI)],
                 "noah", AUJOURDHUI, date(2026, 12, 31))
    assert len(c) == 1 and c[0].poste == "MJC + Périscolaire" and (c[0].debut, c[0].fin) == (time(7, 30), time(19, 0))


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


def test_synchro_un_enfant_a_sa_cle_et_son_titre(tmp_path):
    service = FauxService()
    cfg = replace(CFG, dossier_etat=str(tmp_path), fichier_kidsync=str(fichier(tmp_path)))
    assert synchro_kidsync(cfg, service=service, aujourdhui=AUJOURDHUI) == 0
    evs = evenements(service)
    assert [(ev["extendedProperties"]["private"][PROP], ev["summary"], ev["start"]["dateTime"][:16]) for ev in evs] == [
        ("kidsync-lyam", "Lyam — MJC", "2026-11-25T07:30"), ("kidsync-lyam", "Lyam — Périscolaire", "2026-12-07T16:30"),
        ("kidsync-noah", "Noah — MJC", "2026-11-25T07:30"), ("kidsync-noah", "Noah — Périscolaire", "2026-12-07T16:30"),
    ]
    assert {ev["extendedProperties"]["private"]["categorie"] for ev in evs} == {"enfants"}
    assert all("colorId" not in ev for ev in evs)


def test_second_passage_ne_change_rien(tmp_path):
    service = FauxService()
    cfg = replace(CFG, dossier_etat=str(tmp_path), fichier_kidsync=str(fichier(tmp_path)))
    synchro_kidsync(cfg, service=service, aujourdhui=AUJOURDHUI)
    avant = {k: dict(v) for k, v in service.events().stock.items()}
    synchro_kidsync(cfg, service=service, aujourdhui=AUJOURDHUI)
    assert service.events().stock == avant


def test_une_inscription_qui_passe_en_ecart_est_retiree(tmp_path):
    service = FauxService()
    cfg = replace(CFG, dossier_etat=str(tmp_path), fichier_kidsync=str(fichier(tmp_path)))
    synchro_kidsync(cfg, service=service, aujourdhui=AUJOURDHUI)
    retire = [x if not (x["enfant"] == "noah" and x["date"] == "2026-11-25") else {**x, "statut": "ecart"} for x in ENTREES]
    cfg = replace(cfg, fichier_kidsync=str(fichier(tmp_path, retire)))
    synchro_kidsync(cfg, service=service, aujourdhui=AUJOURDHUI)
    titres = [ev["summary"] for ev in evenements(service)]
    assert "Noah — MJC" not in titres and "Lyam — MJC" in titres


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
