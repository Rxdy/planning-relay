from datetime import date, timedelta

import pytest

from planning_relay.agenda import creneau_depuis_proprietes, evenement
from planning_relay.alerte import Compteur, doit_alerter
from planning_relay.config import Config
from planning_relay.modeles import creneau_depuis_cellule as cc
from planning_relay.synchro import PlanningRetire, fenetre, passage

LUNDI = date(2026, 10, 5)
MARDI = date(2026, 10, 6)


def cfg(**kw):
    env = {"PERSON_NAME": "Charlène", "FETCH_RETRY_DELAY": "0"}
    env.update(kw)
    return Config.depuis_env(env)


class Connecteur:
    def __init__(self, creneaux):
        self.creneaux = creneaux

    def recuperer(self, debut, fin):
        return self.creneaux


class Agenda:
    def __init__(self, connus):
        self.connus, self.appliques = connus, None

    def lister(self, debut, fin):
        return {j: c for j, c in self.connus.items() if debut <= j <= fin}

    def appliquer(self, changements, aujourdhui=None):
        self.appliques = changements

    def rafraichir_titres(self):
        return 0


class Messagerie:
    def __init__(self):
        self.envois = []

    def par_semaine(self, planning, connus, changements, aujourdhui):
        self.envois.append(changements)


def test_fenetre():
    assert fenetre(date(2026, 10, 7), 4) == (date(2026, 10, 7), date(2026, 11, 1))


def test_rien_ne_change_ni_mail_ni_ecriture():
    c = cc(MARDI, "SOIR", "14:45 - 22:45")
    agenda, mail = Agenda({MARDI: c}), Messagerie()
    assert passage(cfg(), Connecteur([c]), agenda, mail, LUNDI) == []
    assert mail.envois == [] and agenda.appliques is None


def test_un_changement_un_mail_puis_ecriture():
    agenda, mail = Agenda({}), Messagerie()
    ch = passage(cfg(), Connecteur([cc(MARDI, "SOIR", "14:45 - 22:45")]), agenda, mail, LUNDI)
    assert len(mail.envois) == 1 and agenda.appliques == ch


def test_jours_passes_ignores():
    agenda, mail = Agenda({LUNDI: cc(LUNDI, "R")}), Messagerie()
    assert passage(cfg(), Connecteur([cc(LUNDI, "R")]), agenda, mail, MARDI) == []


def test_garde_fou_planning_vide():
    agenda = Agenda({MARDI: cc(MARDI, "R")})
    with pytest.raises(PlanningRetire):
        passage(cfg(), Connecteur([]), agenda, Messagerie(), LUNDI)
    assert agenda.appliques is None


def test_codes_ignores():
    agenda = Agenda({})
    passage(cfg(SKIP_CODES="r, rf"), Connecteur([cc(MARDI, "RF")]), agenda, Messagerie(), LUNDI)
    assert agenda.appliques is None


def test_dry_run_ne_touche_a_rien():
    agenda, mail = Agenda({}), Messagerie()
    assert passage(cfg(DRY_RUN="1"), Connecteur([cc(MARDI, "SOIR", "14:45 - 22:45")]), agenda, mail, LUNDI)
    assert mail.envois == [] and agenda.appliques is None


def test_aller_retour_proprietes_agenda():
    c = cc(MARDI, "NIGHT", "22:45 - 07:00 (7h45), pause 30 min")
    ev = evenement(c, cfg())
    assert ev["summary"] == "Charlène — Nuit"
    assert ev["end"]["dateTime"] == "2026-10-07T07:00:00"
    assert creneau_depuis_proprietes(ev["extendedProperties"]["private"]) == c


def test_evenement_journee_entiere():
    ev = evenement(cc(MARDI, "R"), cfg())
    assert ev["start"] == {"date": "2026-10-06"} and ev["end"] == {"date": "2026-10-07"}


def test_alerte_seulement_au_troisieme_echec():
    assert [doit_alerter(n) for n in range(1, 6)] == [False, False, True, False, False]


def test_compteur_echecs(tmp_path):
    c = Compteur(str(tmp_path / "etat"))
    assert c.lire() == 0
    assert [c.echec(), c.echec()] == [1, 2]
    c.succes()
    assert c.lire() == 0 and c.echec() == 1


def test_sync_alerte_au_troisieme_echec_a_l_admin_seul_puis_retabli(tmp_path, monkeypatch):
    import planning_relay.__main__ as m
    import planning_relay.synchro as s
    import planning_relay.agenda as a
    import planning_relay.connecteurs as co
    import planning_relay.mail as ml
    from planning_relay.connecteurs.silae import IdentifiantsRefuses

    envois = []
    monkeypatch.setattr(co, "creer_connecteur", lambda cfg: None)
    monkeypatch.setattr(a, "Agenda", lambda cfg: None)
    monkeypatch.setattr(ml.Messagerie, "envoyer",
                        lambda self, objet, texte, html=None, dest=None: envois.append((objet, texte, dest)))
    monkeypatch.setattr(s, "passage", lambda *args: (_ for _ in ()).throw(IdentifiantsRefuses("x")))
    config = cfg(STATE_DIR=str(tmp_path), MAIL_TO="rudy@x.fr,charlene@x.fr", SMTP_USER="rudy@x.fr")
    assert [m.synchro(config) for _ in range(4)] == [1, 1, 1, 1]
    [(objet, texte, dest)] = envois
    assert dest == ["rudy@x.fr"]  # jamais Charlène
    assert objet == "⚠ planning-relay en panne : Silae refuse la connexion de Charlène"
    assert "mot de passe sirh.software a probablement changé" in texte and "PLATFORM_PASSWORD" in texte

    monkeypatch.setattr(s, "passage", lambda *args: [])
    assert m.synchro(config) == 0
    assert envois[-1][0] == "✓ planning-relay refonctionne" and envois[-1][2] == ["rudy@x.fr"]
    assert "après 4 échecs" in envois[-1][1]
    assert Compteur(str(tmp_path)).lire() == 0
    assert m.synchro(config) == 0 and len(envois) == 2  # pas de second mail de rétablissement


def test_alert_to_configurable():
    assert cfg(SMTP_USER="rudy@x.fr").alerte_to == ["rudy@x.fr"]
    assert cfg(SMTP_USER="rudy@x.fr", ALERT_TO="rudy@x.fr, charlene@x.fr").alerte_to == ["rudy@x.fr", "charlene@x.fr"]


def test_repos_ignores_par_defaut_meme_variable_vide():
    # Le workflow passe SKIP_CODES vide quand la variable GitHub n'existe pas
    agenda = Agenda({})
    passage(cfg(SKIP_CODES=""), Connecteur([cc(MARDI, "R"), cc(LUNDI + timedelta(days=2), "RF")]),
            agenda, Messagerie(), LUNDI)
    assert agenda.appliques is None



def test_planning_retire_ni_panne_ni_mail(tmp_path, monkeypatch):
    import planning_relay.__main__ as m
    import planning_relay.synchro as s
    import planning_relay.agenda as a
    import planning_relay.connecteurs as co
    import planning_relay.mail as ml

    envois = []
    monkeypatch.setattr(co, "creer_connecteur", lambda cfg: None)
    monkeypatch.setattr(a, "Agenda", lambda cfg: None)
    monkeypatch.setattr(ml.Messagerie, "envoyer", lambda self, *args, **kw: envois.append(args[0]))
    monkeypatch.setattr(s, "passage", lambda *args: (_ for _ in ()).throw(PlanningRetire(equipe_vide=True)))
    config = cfg(STATE_DIR=str(tmp_path), SMTP_USER="rudy@x.fr")

    assert [m.synchro(config) for _ in range(5)] == [0] * 5
    assert envois == []
    assert Compteur(str(tmp_path)).lire() == 0  # healthcheck reste sain

    monkeypatch.setattr(s, "passage", lambda *args: [])  # le planning revient
    assert m.synchro(config) == 0 and envois == []


def test_planning_retire_pendant_une_panne_clot_la_panne(tmp_path, monkeypatch):
    import planning_relay.__main__ as m
    import planning_relay.synchro as s
    import planning_relay.agenda as a
    import planning_relay.connecteurs as co
    import planning_relay.mail as ml

    envois = []
    monkeypatch.setattr(co, "creer_connecteur", lambda cfg: None)
    monkeypatch.setattr(a, "Agenda", lambda cfg: None)
    monkeypatch.setattr(ml.Messagerie, "envoyer",
                        lambda self, objet, texte, html=None, dest=None: envois.append(objet))
    monkeypatch.setattr(s, "passage", lambda *args: (_ for _ in ()).throw(RuntimeError("?")))
    config = cfg(STATE_DIR=str(tmp_path), SMTP_USER="rudy@x.fr")
    for _ in range(3):
        m.synchro(config)
    monkeypatch.setattr(s, "passage", lambda *args: (_ for _ in ()).throw(PlanningRetire(equipe_vide=False)))
    m.synchro(config)
    assert envois[1:] == ["✓ planning-relay refonctionne"]
