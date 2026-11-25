"""Tableau de bord : validation, écriture dans le volume, historique, et prise en compte par les passages."""

import json
import threading
from dataclasses import replace
from datetime import datetime
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

from planning_relay import tableau
from planning_relay.config import HORAIRES_PAR_DEFAUT, Config
from planning_relay.horaires import charger, fichier_actif


@pytest.fixture
def cfg(tmp_path):
    return Config.depuis_env({"CONNECTOR": "horaires", "STATE_DIR": str(tmp_path)})


def champs_lea(**modif):
    """Le formulaire tel que le navigateur l'enverrait, pour Léa (p1), avec 09:00-16:30 sauf mercredi."""
    c = {}
    for i, p in enumerate(charger(HORAIRES_PAR_DEFAUT)):
        if p["type"] == "fixed":
            for jour, creneaux in p["schedule"].items():
                d, _, f = (creneaux[0] if creneaux else "").partition("-")
                c[f"p{i}_{jour}_debut"], c[f"p{i}_{jour}_fin"] = [d], [f]
        elif p["type"] == "allday":
            for j in p["days"]:
                c[f"p{i}_jour_{j}"] = ["on"]
            for k, a in enumerate(p["alternating"]):
                c[f"p{i}_alt{k}"] = [a["type"]]
    c.update({k: [v] for k, v in modif.items()})
    return c


def test_sans_modification_les_passages_lisent_le_fichier_de_l_image(cfg):
    assert fichier_actif(cfg) == HORAIRES_PAR_DEFAUT


def test_enregistrement_ecrit_dans_le_volume_et_les_passages_le_preferent(cfg, tmp_path):
    regles = tableau.appliquer_formulaire(tableau.lire(cfg), champs_lea(p1_monday_debut="10:00"))
    tableau.enregistrer(cfg, regles)
    assert fichier_actif(cfg) == tmp_path / "horaires.json"
    lea = next(p for p in tableau.lire(cfg) if p["name"] == "Léa")
    assert lea["schedule"]["monday"] == ["10:00-16:30"]
    # même vu depuis le passage « horaires », dont le dossier d'état est un sous-dossier
    assert fichier_actif(replace(cfg, dossier_etat=str(tmp_path / "horaires"))) == tmp_path / "horaires.json"


def test_horaires_file_garde_la_priorite(tmp_path):
    cfg = Config.depuis_env({"STATE_DIR": str(tmp_path), "HORAIRES_FILE": "/autre.json"})
    (tmp_path / "horaires.json").write_text("[]")
    assert str(fichier_actif(cfg)) == "/autre.json"


def test_le_formulaire_ne_touche_pas_aux_rotations(cfg):
    avant = tableau.lire(cfg)
    apres = tableau.appliquer_formulaire(avant, champs_lea())
    assert apres == avant


def test_poubelle_modifiable_jour_et_couleurs(cfg):
    i = [p["name"] for p in tableau.lire(cfg)].index("Poubelle")
    c = champs_lea()
    c.pop(f"p{i}_jour_thursday")
    c[f"p{i}_jour_wednesday"] = ["on"]
    c[f"p{i}_alt0"] = ["Verte"]
    poubelle = tableau.appliquer_formulaire(tableau.lire(cfg), c)[i]
    assert poubelle["days"] == ["wednesday"]
    assert poubelle["alternating"][0] == {"weekMod2": 0, "type": "Verte"}


def test_un_horaire_incomplet_est_refuse(cfg):
    with pytest.raises(ValueError, match="début ET de fin"):
        tableau.appliquer_formulaire(tableau.lire(cfg), champs_lea(p1_monday_fin=""))


@pytest.mark.parametrize("mauvais", [[], {}, [{"name": "X", "type": "inconnu"}],
                                     [{"name": "A", "type": "fixed"}, {"name": "A", "type": "fixed"}],
                                     [{"name": "A", "type": "fixed", "schedule": {j: ["9h-17h"] for j in
                                       ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]}}],
                                     [{"name": "A", "type": "fixed", "schedule": {
                                         "monday": ["09:00-10:00", "11:00-12:00"]}}]])
def test_validation_refuse_ce_que_les_passages_ne_sauraient_pas_lire(mauvais, cfg):
    with pytest.raises(ValueError):
        tableau.enregistrer(cfg, mauvais)
    assert fichier_actif(cfg) == HORAIRES_PAR_DEFAUT  # rien d'écrit


def test_historique_et_restauration(cfg):
    base = tableau.lire(cfg)
    tableau.enregistrer(cfg, base, datetime(2026, 11, 25, 18, 0, 0))
    modif = tableau.appliquer_formulaire(base, champs_lea(p1_monday_debut="10:00"))
    tableau.enregistrer(cfg, modif, datetime(2026, 11, 25, 18, 5, 0))
    assert tableau.sauvegardes(cfg) == ["horaires-20261125-180500.json"]
    tableau.restaurer(cfg, "horaires-20261125-180500.json")
    assert tableau.lire(cfg) == base


@pytest.mark.parametrize("nom", ["../../etc/passwd", "horaires-1.json", ""])
def test_restauration_refuse_les_noms_douteux(cfg, nom):
    with pytest.raises(ValueError):
        tableau.restaurer(cfg, nom)


def test_revenir_a_l_image(cfg):
    tableau.enregistrer(cfg, tableau.lire(cfg))
    tableau.revenir_a_l_image(cfg)
    assert fichier_actif(cfg) == HORAIRES_PAR_DEFAUT


def test_la_page_echappe_les_noms_et_garde_le_jeton(cfg):
    regles = tableau.lire(cfg)
    regles[0] = {**regles[0], "name": "<script>x</script>"}
    html = tableau.page(regles, "JETON", erreur="<b>ko</b>")
    assert "<script>x" not in html and "&lt;script&gt;" in html and "&lt;b&gt;ko" in html
    assert html.count('name="jeton" value="JETON"') >= 2


# --- HTTP ------------------------------------------------------------------------

@pytest.fixture
def serveur(cfg, monkeypatch):
    monkeypatch.setattr(tableau.horloge, "sleep", lambda s: None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), tableau._handler(cfg, "secret", "JT"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def requete(port, methode, chemin, corps=None, mdp="secret"):
    import base64
    h = {"Authorization": "Basic " + base64.b64encode(f"x:{mdp}".encode()).decode()} if mdp is not None else {}
    if corps is not None:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    c = HTTPConnection("127.0.0.1", port)
    c.request(methode, chemin, urlencode(corps, doseq=True) if corps is not None else None, h)
    r = c.getresponse()
    return r.status, r.read().decode()


def test_http_mot_de_passe_obligatoire(serveur):
    assert requete(serveur, "GET", "/", mdp=None)[0] == 401
    assert requete(serveur, "GET", "/", mdp="faux")[0] == 401
    assert requete(serveur, "GET", "/")[0] == 200


def test_http_jeton_obligatoire_pour_modifier(serveur, cfg):
    statut, _ = requete(serveur, "POST", "/enregistrer", {"jeton": "FAUX", **champs_lea(p1_monday_debut="10:00")})
    assert statut == 403 and fichier_actif(cfg) == HORAIRES_PAR_DEFAUT


def test_http_enregistrement_complet(serveur, cfg):
    statut, html = requete(serveur, "POST", "/enregistrer", {"jeton": "JT", **champs_lea(p1_monday_debut="10:00")})
    assert statut == 200 and "Enregistré." in html
    assert next(p for p in tableau.lire(cfg) if p["name"] == "Léa")["schedule"]["monday"] == ["10:00-16:30"]


def test_http_erreur_affichee_sans_rien_ecrire(serveur, cfg):
    statut, html = requete(serveur, "POST", "/brut", {"jeton": "JT", "json": "{pas du json"})
    assert statut == 200 and "JSON invalide" in html and fichier_actif(cfg) == HORAIRES_PAR_DEFAUT
