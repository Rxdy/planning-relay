"""Horaires en dur : calcul des postes (comme AbView) et recopie exacte dans l'agenda."""

from dataclasses import replace
from datetime import date, time

import pytest

from planning_relay.agenda import PROP, Agenda
from planning_relay.config import HORAIRES_PAR_DEFAUT, Config
from planning_relay.horaires import charger, cle, creneaux, horaires_du_jour, passage_horaires, synchro_horaires

from test_agenda import FauxService

REGLES = {p["name"]: p for p in charger(HORAIRES_PAR_DEFAUT)}
CFG = Config.depuis_env({"CONNECTOR": "horaires", "CALENDAR_ID": "affichage@group", "WEEKS": "2"})
LUNDI = date(2026, 10, 12)  # semaine ISO 42


def semaine(nom: str, lundi: date = LUNDI) -> dict[str, tuple[str, str] | None]:
    """Jour → (poste, "HH:MM-HH:MM") ou None, du lundi au dimanche."""
    jours = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
    par_jour = {c.jour: c for c in creneaux(REGLES[nom], lundi, date.fromordinal(lundi.toordinal() + 6))}
    return {
        j: (c.poste, f"{c.debut:%H:%M}-{c.fin:%H:%M}") if (c := par_jour.get(date.fromordinal(lundi.toordinal() + i))) else None
        for i, j in enumerate(jours)
    }


# --- Calcul, semaine du 12/10/2026 telle qu'affichée par AbView ------------------

def test_fichier_livre_les_quatre_personnes():
    assert list(REGLES) == ["Rudy", "Léa", "Luis", "Caroline", "Poubelle"]


def test_lea_horaires_fixes_mercredi_different():
    assert semaine("Léa") == {
        "lun": ("", "08:30-16:30"), "mar": ("", "08:30-16:30"), "mer": ("", "11:15-18:30"),
        "jeu": ("", "08:30-16:30"), "ven": ("", "08:30-16:30"), "sam": None, "dim": None,
    }


def test_luis_rotation_par_decalage():
    # (42 + 2) % 3 = 2 → 3e semaine : Matin
    assert semaine("Luis") == {
        "lun": ("Matin", "05:00-13:00"), "mar": ("Matin", "05:00-13:00"), "mer": ("Matin", "05:00-13:00"),
        "jeu": ("Matin", "05:00-13:00"), "ven": ("Matin", "05:00-13:00"), "sam": None, "dim": None,
    }


def test_luis_semaine_de_nuit_sans_vendredi():
    # Semaine 43 : (43 + 2) % 3 = 0 → Nuit, vendredi en exception vide
    s = semaine("Luis", date(2026, 10, 19))
    assert s["jeu"] == ("Nuit", "21:00-05:00")
    assert s["ven"] is None


def test_caroline_cycle_ancre_et_vendredi_raccourci():
    # Ancre 2026-S13 position 0 ; S42 → (42 - 13) % 9 = 2 → Nuit
    assert semaine("Caroline") == {
        "lun": ("Nuit", "21:00-05:00"), "mar": ("Nuit", "21:00-05:00"), "mer": ("Nuit", "21:00-05:00"),
        "jeu": ("Nuit", "21:00-05:00"), "ven": ("Nuit", "19:00-02:00"), "sam": None, "dim": None,
    }


def test_poubelle_le_jeudi_jaune_et_noire_en_alternance():
    # S42 paire → Jaune, S43 impaire → Noire ; journée entière, catégorie poubelle
    jeudis = creneaux(REGLES["Poubelle"], LUNDI, date(2026, 10, 25))
    assert [(c.jour, c.poste, c.journee_entiere, c.categorie) for c in jeudis] == [
        (date(2026, 10, 15), "Jaune", True, "poubelle"),
        (date(2026, 10, 22), "Noire", True, "poubelle"),
    ]


def test_semaine_modifiee_remplace_la_rotation():
    # 2026-S4 est forcée en Nuit alors que l'ancre la placerait ailleurs
    assert horaires_du_jour(REGLES["Caroline"], date(2026, 1, 20)) == ("Nuit", ["21:00-05:00"])


def test_nuit_finit_le_lendemain():
    nuit = creneaux(REGLES["Caroline"], LUNDI, LUNDI)[0]
    debut, fin = nuit.bornes()
    assert (debut.date(), fin.date(), fin.time()) == (LUNDI, date(2026, 10, 13), time(5, 0))


def test_deux_semaines_couvrent_les_8_jours_d_abview_quel_que_soit_le_jour():
    from datetime import timedelta

    from planning_relay.synchro import fenetre

    for decalage in range(7):  # du lundi au dimanche
        jour = LUNDI + timedelta(days=decalage)
        debut, fin = fenetre(jour, 2)
        assert debut == jour
        assert 7 <= (fin - jour).days <= 13


def test_cle_sans_accents():
    assert cle("Léa") == "horaires-lea"
    assert cle("Caroline") == "horaires-caroline"


def test_regles_invalides(tmp_path):
    f = tmp_path / "h.json"
    f.write_text('[{"name": "X", "type": "inconnu"}]')
    with pytest.raises(ValueError):
        charger(f)


# --- Recopie dans l'agenda -------------------------------------------------------

def _agenda(nom: str, service=None) -> tuple[Config, Agenda]:
    pcfg = replace(CFG, personne=nom, cle_synchro=cle(nom))
    return pcfg, Agenda(pcfg, service=service or FauxService())


def _stock(agenda):
    return agenda.service.events().stock


def test_publication_titres_categorie_sans_couleur():
    cfg, agenda = _agenda("Luis")
    passage_horaires(cfg, REGLES["Luis"], agenda, LUNDI)
    evs = sorted(_stock(agenda).values(), key=lambda e: e["start"]["dateTime"])
    assert evs[0]["summary"] == "Luis — Matin"
    props = evs[0]["extendedProperties"]["private"]
    assert (props[PROP], props["categorie"], props["poste"]) == ("horaires-luis", "travail", "Matin")
    assert "colorId" not in evs[0]
    assert evs[0]["description"].endswith("Horaires fixes, publiés par planning-relay.")


def test_horaires_fixes_titre_prenom_seul():
    cfg, agenda = _agenda("Léa")
    passage_horaires(cfg, REGLES["Léa"], agenda, LUNDI)
    titres = {e["summary"] for e in _stock(agenda).values()}
    assert titres == {"Léa"}
    assert len(_stock(agenda)) == 10  # 2 semaines, du lundi au vendredi


def test_poubelle_journee_entiere_dans_l_agenda():
    cfg, agenda = _agenda("Poubelle")
    passage_horaires(cfg, REGLES["Poubelle"], agenda, LUNDI)
    evs = sorted(_stock(agenda).values(), key=lambda e: e["start"]["date"])
    assert [(e["summary"], e["start"], e["end"]) for e in evs] == [
        ("Poubelle — Jaune", {"date": "2026-10-15"}, {"date": "2026-10-16"}),
        ("Poubelle — Noire", {"date": "2026-10-22"}, {"date": "2026-10-23"}),
    ]
    assert {e["extendedProperties"]["private"]["categorie"] for e in evs} == {"poubelle"}
    # Relue depuis l'agenda, la catégorie ne déclenche ni correction ni changement
    assert passage_horaires(cfg, REGLES["Poubelle"], agenda, LUNDI) == []
    assert not [a for a in agenda.service.events().appels if a[0] == "patch"]


def test_second_passage_ne_change_rien():
    cfg, agenda = _agenda("Caroline")
    passage_horaires(cfg, REGLES["Caroline"], agenda, LUNDI)
    assert passage_horaires(cfg, REGLES["Caroline"], agenda, LUNDI) == []


def test_modification_et_suppression_recopiees_sans_mention():
    cfg, agenda = _agenda("Léa")
    passage_horaires(cfg, REGLES["Léa"], agenda, LUNDI)
    modifiee = {**REGLES["Léa"], "schedule": {**REGLES["Léa"]["schedule"], "monday": ["09:00-17:00"], "friday": []}}

    chs = passage_horaires(cfg, modifiee, agenda, LUNDI)

    assert sorted(ch.type for ch in chs) == ["modif", "modif", "suppr", "suppr"]
    evs = list(_stock(agenda).values())
    assert len(evs) == 8
    assert all(e["summary"] == "Léa" for e in evs)  # pas de « (modifié) »
    lundis = [e for e in evs if e["start"]["dateTime"].startswith("2026-10-12")]
    assert lundis[0]["start"]["dateTime"].startswith("2026-10-12T09:00")


def test_ne_touche_ni_silae_ni_saisies_manuelles():
    service = FauxService()
    stock = service.events().stock
    stock["charlene"] = {"id": "charlene", "summary": "Charlène — Matin",
                         "extendedProperties": {"private": {PROP: "charlene", "jour": "2026-10-12", "code": "MATIN"}}}
    stock["manuel"] = {"id": "manuel", "summary": "Dentiste"}
    cfg, agenda = _agenda("Léa", service)
    passage_horaires(cfg, {**REGLES["Léa"], "schedule": {}}, agenda, LUNDI)
    assert {"charlene", "manuel"} <= set(stock)


def test_dry_run_n_ecrit_rien():
    cfg, agenda = _agenda("Rudy")
    cfg = replace(cfg, dry_run=True)
    agenda.cfg = cfg
    assert len(passage_horaires(cfg, REGLES["Rudy"], agenda, LUNDI)) == 10
    assert _stock(agenda) == {}


def test_synchro_toutes_les_personnes_sans_mail(tmp_path):
    service = FauxService()
    cfg = replace(CFG, dossier_etat=str(tmp_path))
    assert synchro_horaires(cfg, service=service, aujourdhui=LUNDI) == 0
    cles = {e["extendedProperties"]["private"][PROP] for e in service.events().stock.values()}
    assert cles == {"horaires-rudy", "horaires-lea", "horaires-luis", "horaires-caroline", "horaires-poubelle"}


def test_synchro_travail_en_paon_et_poubelle_sans_couleur(tmp_path):
    service = FauxService()
    cfg = replace(CFG, dossier_etat=str(tmp_path), couleur="7")
    synchro_horaires(cfg, service=service, aujourdhui=LUNDI)
    couleurs = {(e["extendedProperties"]["private"][PROP], e.get("colorId")) for e in service.events().stock.values()}
    assert couleurs == {("horaires-rudy", "7"), ("horaires-lea", "7"), ("horaires-luis", "7"),
                        ("horaires-caroline", "7"), ("horaires-poubelle", None)}


def test_synchro_compte_les_echecs(tmp_path):
    cfg = replace(CFG, dossier_etat=str(tmp_path), fichier_horaires=str(tmp_path / "absent.json"))
    assert synchro_horaires(cfg, service=FauxService(), aujourdhui=LUNDI) == 1
    from planning_relay.alerte import Compteur

    assert Compteur(str(tmp_path)).lire() == 1
