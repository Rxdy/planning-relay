from datetime import date

from planning_relay.comparaison import comparer
from planning_relay.config import Config
from planning_relay.mail import mails
from planning_relay.modeles import creneau_depuis_cellule as cc

CFG = Config.depuis_env({"PERSON_NAME": "Charlène"})
LUN41, MAR41, LUN42, MER42 = date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 12), date(2026, 10, 14)


def test_une_semaine_publiee_un_mail_publiee():
    planning = {LUN42: cc(LUN42, "SOIR", "14:45 - 22:45"), MER42: cc(MER42, "R")}
    [m] = mails(CFG, planning, {}, comparer({}, planning), LUN41)
    assert m.objet == "Planning de Charlène · sem. 42 (12–18 oct.) · publiée"
    assert "lun. 12/10" in m.texte and "dim. 18/10" in m.texte and "avant" not in m.texte


def test_modif_ne_renvoie_que_la_semaine_concernee():
    avant = {MAR41: cc(MAR41, "R"), MER42: cc(MER42, "R")}
    apres = {MAR41: cc(MAR41, "R"), MER42: cc(MER42, "NIGHT", "22:45 - 07:00")}
    [m] = mails(CFG, apres, avant, comparer(avant, apres), LUN41)
    assert m.objet == "Planning de Charlène · sem. 42 (12–18 oct.) · 1 changement"
    assert "* mer. 14/10   NIGHT 22:45–07:00" in m.texte and "(avant : R)" in m.texte
    assert "<s>R</s>" in m.html


def test_deux_semaines_touchees_deux_mails():
    avant = {MAR41: cc(MAR41, "R"), MER42: cc(MER42, "R")}
    apres = {}
    objets = [m.objet for m in mails(CFG, apres, avant, comparer(avant, apres), LUN41)]
    assert objets == [
        "Planning de Charlène · sem. 41 (5–11 oct.) · 1 changement",
        "Planning de Charlène · sem. 42 (12–18 oct.) · 1 changement",
    ]


def test_semaine_a_cheval_sur_deux_mois():
    lundi = date(2026, 10, 26)
    planning = {lundi: cc(lundi, "R")}
    [m] = mails(CFG, planning, {}, comparer({}, planning), LUN41)
    assert "(26 oct. – 1 nov.)" in m.objet


def test_jours_passes_marques():
    planning = {LUN41: cc(LUN41, "SOIR", "14:45 - 22:45"), MAR41: cc(MAR41, "R")}
    [m] = mails(CFG, planning, {}, comparer({}, {MAR41: planning[MAR41]}), MAR41)
    assert "lun. 05/10   SOIR 14:45–22:45" in m.texte and "[passé]" in m.texte


def test_jour_ajoute_dans_semaine_connue_n_est_pas_publiee():
    avant = {MAR41: cc(MAR41, "R")}
    apres = {**avant, date(2026, 10, 7): cc(date(2026, 10, 7), "R")}
    [m] = mails(CFG, apres, avant, comparer(avant, apres), LUN41)
    assert m.objet.endswith("· 1 changement") and "* mer. 07/10" in m.texte
