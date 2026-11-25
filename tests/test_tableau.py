"""Tableau de bord : validation, écriture dans le volume, historique, et prise en compte par les passages."""

import json
import threading
from datetime import date
from html.parser import HTMLParser
from dataclasses import replace
from datetime import datetime
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

from planning_relay import tableau
from planning_relay.config import HORAIRES_PAR_DEFAUT, Config
from planning_relay.horaires import charger, fichier_actif, horaires_du_jour


AUJOURDHUI = date(2026, 10, 12)  # semaine 42


@pytest.fixture
def cfg(tmp_path):
    return Config.depuis_env({"CONNECTOR": "horaires", "STATE_DIR": str(tmp_path)})


class _Champs(HTMLParser):
    """Les valeurs qu'un navigateur renverrait en validant le formulaire principal sans rien toucher."""

    def __init__(self):
        super().__init__()
        self.valeurs: dict[str, list[str]] = {}
        self.dans = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.dans = a.get("action") == "/enregistrer"
        if not self.dans or tag != "input" or "name" not in a:
            return
        genre = a.get("type", "text")
        if genre in {"checkbox", "radio"}:
            if "checked" in a:
                self.valeurs.setdefault(a["name"], []).append(a.get("value", "on"))
        else:
            self.valeurs[a["name"]] = [a.get("value", "")]


def formulaire(cfg, **modif):
    """Champs du formulaire affiché pour les horaires en vigueur ; modif remplace des champs (None : décoche)."""
    f = _Champs()
    f.feed(tableau.page(tableau.lire(cfg), "JT", aujourdhui=AUJOURDHUI))
    champs = f.valeurs
    for k, v in modif.items():
        if v is None:
            champs.pop(k, None)
        else:
            champs[k] = [v]
    return champs


def appliquer(cfg, **modif):
    return tableau.appliquer_formulaire(tableau.lire(cfg), formulaire(cfg, **modif))


def idx(cfg, nom):
    return [p["name"] for p in tableau.lire(cfg)].index(nom)


def test_sans_modification_les_passages_lisent_le_fichier_de_l_image(cfg):
    assert fichier_actif(cfg) == HORAIRES_PAR_DEFAUT


def test_enregistrement_ecrit_dans_le_volume_et_les_passages_le_preferent(cfg, tmp_path):
    tableau.enregistrer(cfg, appliquer(cfg, p1_g0_debut="10:00"))
    assert fichier_actif(cfg) == tmp_path / "horaires.json"
    lea = next(p for p in tableau.lire(cfg) if p["name"] == "Léa")
    assert lea["schedule"]["monday"] == lea["schedule"]["friday"] == ["10:00-16:30"]  # la ligne « lun mar jeu ven »
    assert lea["schedule"]["wednesday"] == ["08:30-16:30"]
    # même vu depuis le passage « horaires », dont le dossier d'état est un sous-dossier
    assert fichier_actif(replace(cfg, dossier_etat=str(tmp_path / "horaires"))) == tmp_path / "horaires.json"


def test_horaires_file_garde_la_priorite(tmp_path):
    cfg = Config.depuis_env({"STATE_DIR": str(tmp_path), "HORAIRES_FILE": "/autre.json"})
    (tmp_path / "horaires.json").write_text("[]")
    assert str(fichier_actif(cfg)) == "/autre.json"


def test_formulaire_inchange_ne_change_rien_meme_pour_les_rotations(cfg):
    assert appliquer(cfg) == tableau.lire(cfg)


def test_lea_se_lit_en_deux_lignes(cfg):
    champs = formulaire(cfg)
    i = idx(cfg, "Léa")
    assert champs[f"p{i}_g0_debut"] == ["09:00"] and champs[f"p{i}_g0_fin"] == ["16:30"]
    assert [j for j in ("monday", "tuesday", "wednesday", "thursday", "friday") if f"p{i}_g0_{j}" in champs] == \
        ["monday", "tuesday", "thursday", "friday"]
    assert champs[f"p{i}_g1_debut"] == ["08:30"] and f"p{i}_g1_wednesday" in champs


def test_retirer_un_jour_d_une_ligne_en_fait_un_repos(cfg):
    lea = next(p for p in appliquer(cfg, **{"p1_g1_wednesday": None, "p1_g1_debut": "", "p1_g1_fin": ""}) if p["name"] == "Léa")
    assert lea["schedule"]["wednesday"] == [] and lea["schedule"]["monday"] == ["09:00-16:30"]


def test_un_jour_dans_deux_lignes_est_refuse(cfg):
    with pytest.raises(ValueError, match="deux lignes"):
        appliquer(cfg, **{"p1_g0_wednesday": "on"})


def test_une_ligne_sans_jour_ou_sans_heure_est_refusee(cfg):
    with pytest.raises(ValueError, match="cocher des jours ET"):
        appliquer(cfg, p1_g2_debut="08:00", p1_g2_fin="12:00")
    with pytest.raises(ValueError, match="début ET de fin"):
        appliquer(cfg, p1_g0_fin="")


def test_poubelle_un_jour_et_les_couleurs(cfg):
    i = idx(cfg, "Poubelle")
    poubelle = appliquer(cfg, **{f"p{i}_jour": "wednesday", f"p{i}_alt0": "Verte"})[i]
    assert poubelle["days"] == ["wednesday"]
    assert poubelle["alternating"][0] == {"weekMod2": 0, "type": "Verte"}


def test_rotation_un_jour_different_devient_une_exception(cfg):
    i = idx(cfg, "Luis")
    luis = appliquer(cfg, **{f"p{i}_s0_monday_debut": "20:00"})[i]
    assert luis["rotation"][0]["hours"] == ["21:00-05:00"]
    assert luis["rotation"][0]["exceptions"] == {"monday": ["20:00-05:00"], "friday": [], "saturday": [], "sunday": []}


def test_rotation_changer_la_position_pose_une_ancre(cfg):
    i = idx(cfg, "Luis")
    avant = tableau.lire(cfg)[i]
    assert horaires_du_jour(avant, date(2026, 10, 12))[0] == "Matin"   # (42 + 2) % 3 = 2 → semaine 3 du cycle
    assert formulaire(cfg)[f"p{i}_pos"] == ["3"]
    luis = appliquer(cfg, **{f"p{i}_pos": "1"})[i]
    assert luis["cycleAnchor"] == {"year": 2026, "week": 42, "position": 0} and "offset" not in luis
    assert horaires_du_jour(luis, date(2026, 10, 12))[0] == "Nuit"
    assert horaires_du_jour(luis, date(2026, 10, 19))[0] == "Soir"


def test_rotation_position_hors_cycle_refusee(cfg):
    i = idx(cfg, "Luis")
    with pytest.raises(ValueError, match="1 à 3"):
        appliquer(cfg, **{f"p{i}_pos": "4"})


def test_rotation_allonger_le_cycle(cfg):
    i = idx(cfg, "Luis")
    luis = appliquer(cfg, **{f"p{i}_s3_poste": "Repos"})[i]
    assert [r["shift"] for r in luis["rotation"]] == ["Nuit", "Soir", "Matin", "Repos"]
    assert luis["rotation"][3]["hours"] == []


def test_rotation_semaine_vide_au_milieu_refusee(cfg):
    i = idx(cfg, "Luis")
    vide = {f"p{i}_s1_poste": ""}
    for j in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
        vide[f"p{i}_s1_{j}_debut"] = vide[f"p{i}_s1_{j}_fin"] = ""
    with pytest.raises(ValueError, match="au milieu"):
        appliquer(cfg, **vide)


def test_passer_une_personne_de_fixe_a_postes_garde_ses_deux_jeux_de_donnees(cfg):
    i = idx(cfg, "Léa")
    lea = appliquer(cfg, **{f"p{i}_type": "shift", f"p{i}_s0_poste": "Jour", f"p{i}_s0_monday_debut": "08:00",
                            f"p{i}_s0_monday_fin": "12:00"})[i]
    assert lea["type"] == "shift" and lea["rotation"][0]["shift"] == "Jour"
    assert lea["schedule"]["wednesday"] == ["08:30-16:30"]  # on peut revenir en arrière
    assert "cycleAnchor" in lea  # pas d'ancre au départ : la semaine en cours est la semaine 1


def test_ajouter_et_supprimer_une_personne(cfg):
    n = len(tableau.lire(cfg))
    champs = {f"p{n}_nom": "Zoé", f"p{n}_g0_monday": "on", f"p{n}_g0_debut": "08:00", f"p{n}_g0_fin": "12:00"}
    neuf = appliquer(cfg, **{k: v for k, v in champs.items()}, **{f"p{idx(cfg, 'Caroline')}_suppr": "on"})
    assert "Caroline" not in [p["name"] for p in neuf]
    zoe = neuf[-1]
    assert zoe["name"] == "Zoé" and zoe["type"] == "fixed" and zoe["schedule"]["monday"] == ["08:00-12:00"]


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
    modif = appliquer(cfg, p1_g0_debut="10:00")
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
    statut, _ = requete(serveur, "POST", "/enregistrer", {**formulaire(cfg, p1_g0_debut="10:00"), "jeton": "FAUX"})
    assert statut == 403 and fichier_actif(cfg) == HORAIRES_PAR_DEFAUT


def test_http_enregistrement_complet(serveur, cfg):
    statut, html = requete(serveur, "POST", "/enregistrer", {"jeton": "JT", **formulaire(cfg, p1_g0_debut="10:00")})
    assert statut == 200 and "Enregistré." in html
    assert next(p for p in tableau.lire(cfg) if p["name"] == "Léa")["schedule"]["monday"] == ["10:00-16:30"]


def test_http_erreur_affichee_sans_rien_ecrire(serveur, cfg):
    statut, html = requete(serveur, "POST", "/brut", {"jeton": "JT", "json": "{pas du json"})
    assert statut == 200 and "JSON invalide" in html and fichier_actif(cfg) == HORAIRES_PAR_DEFAUT
