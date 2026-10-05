from datetime import date, datetime, time

from planning_relay.modeles import creneau_depuis_cellule

J = date(2026, 10, 6)


def test_soir():
    c = creneau_depuis_cellule(J, "soir", "14:45 - 22:45 (7h30), pause 30 min")
    assert (c.code, c.debut, c.fin, c.duree, c.pause) == ("SOIR", time(14, 45), time(22, 45), "7h30", "30 min")
    assert not c.fin_le_lendemain
    assert c.libelle() == "SOIR 14:45–22:45"


def test_night_finit_le_lendemain():
    c = creneau_depuis_cellule(J, "NIGHT", "22:45 - 07:00 (7h45), pause 30 min")
    assert c.fin_le_lendemain
    assert c.bornes() == (datetime(2026, 10, 6, 22, 45), datetime(2026, 10, 7, 7, 0))


def test_repos_et_code_inconnu_sans_horaires_journee_entiere():
    assert creneau_depuis_cellule(J, "R").journee_entiere
    assert creneau_depuis_cellule(J, "XYZ", "formation").journee_entiere


def test_code_inconnu_avec_horaires_devient_horaire():
    c = creneau_depuis_cellule(J, "FORM", "09:00 - 12:00")
    assert not c.journee_entiere and c.duree is None
