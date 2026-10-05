from datetime import date

from planning_relay.comparaison import comparer
from planning_relay.modeles import creneau_depuis_cellule as cc

J1, J2, J3 = date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8)


def test_aucun_ecart():
    p = {J1: cc(J1, "SOIR", "14:45 - 22:45")}
    assert comparer(p, dict(p)) == []


def test_ajout_modif_suppr():
    connus = {J1: cc(J1, "SOIR", "14:45 - 22:45"), J2: cc(J2, "R")}
    lus = {J1: cc(J1, "07H", "07:00 - 15:45"), J3: cc(J3, "NIGHT", "22:45 - 07:00")}
    assert [(c.type, c.jour) for c in comparer(connus, lus)] == [
        ("modif", J1),
        ("suppr", J2),
        ("ajout", J3),
    ]


def test_duree_ou_pause_seules_ne_comptent_pas():
    a = {J1: cc(J1, "SOIR", "14:45 - 22:45 (7h30), pause 30 min")}
    b = {J1: cc(J1, "SOIR", "14:45 - 22:45")}
    assert comparer(a, b) == []
