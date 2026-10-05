from datetime import date
from html.parser import HTMLParser

import pytest

from planning_relay.apercu import AUJOURDHUI, scenarios
from planning_relay.config import Config

CFG = Config.depuis_env({"PERSON_NAME": "Charlène"})
SC = {s.nom: s for s in scenarios()}


def un_mail(nom):
    [m] = SC[nom].mails(CFG)
    return m


JOURS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")


def lignes_semaine(texte):
    """Lignes du tableau de la semaine : « * » ou espace, puis le jour."""
    return [l for l in texte.splitlines() if l[:2] in ("* ", "  ") and l[2:6] in JOURS]


def section(texte, titre):
    """Lignes sous un titre (CE QUI CHANGE, LA SEMAINE) jusqu'à la ligne vide."""
    lignes = texte.splitlines()
    debut = lignes.index(titre) + 1
    fin = lignes.index("", debut) if "" in lignes[debut:] else len(lignes)
    return lignes[debut:fin]


# --- Objet -----------------------------------------------------------------

@pytest.mark.parametrize("nom, objet", [
    ("publiee", "Planning de Charlène · sem. 41 (5–11 oct.) · publiée"),
    ("nouvelle-semaine", "Planning de Charlène · sem. 42 (12–18 oct.) · publiée"),
    ("changement-poste", "Planning de Charlène · sem. 41 (5–11 oct.) · 1 changement"),
    ("plusieurs-changements", "Planning de Charlène · sem. 41 (5–11 oct.) · 3 changements"),
    ("ajout-semaine-connue", "Planning de Charlène · sem. 41 (5–11 oct.) · 1 changement"),
])
def test_objet(nom, objet):
    assert un_mail(nom).objet == objet


def test_deux_semaines_touchees_deux_mails_chacun_sa_semaine():
    m41, m42 = SC["deux-semaines"].mails(CFG)
    assert "sem. 41" in m41.objet and "sem. 42" in m42.objet
    assert "ven. 09/10" in section(m41.texte, "CE QUI CHANGE")[0]
    assert "mer. 14/10" in section(m42.texte, "CE QUI CHANGE")[0]
    assert "14/10" not in m41.texte and "09/10" not in m42.texte


def test_semaine_inchangee_pas_de_mail():
    assert [m.objet for m in SC["nouvelle-semaine"].mails(CFG)] == [
        "Planning de Charlène · sem. 42 (12–18 oct.) · publiée"
    ]


def test_semaine_a_cheval_sur_deux_mois():
    from planning_relay.comparaison import comparer
    from planning_relay.mail import mails
    from planning_relay.apercu import repos

    j = date(2026, 10, 26)
    [m] = mails(CFG, {j: repos(j)}, {}, comparer({}, {j: repos(j)}), AUJOURDHUI)
    assert "(26 oct. – 1 nov.)" in m.objet


# --- Lecture hebdomadaire --------------------------------------------------

@pytest.mark.parametrize("nom", list(SC))
def test_chaque_mail_montre_les_sept_jours_dans_l_ordre(nom):
    for m in SC[nom].mails(CFG):
        assert [l[2:6] for l in lignes_semaine(m.texte)] == list(JOURS)


def test_semaine_publiee_sans_bloc_changement_ni_marque():
    m = un_mail("publiee")
    assert "Nouvelle semaine publiée." in m.texte
    assert "CE QUI CHANGE" not in m.texte and "CE QUI CHANGE" not in m.html
    assert not any(l.startswith("*") for l in m.texte.splitlines())


def test_jours_passes_marques_et_grises():
    m = un_mail("changement-poste")
    assert "lun. 05/10   14:45–22:45   Soir   [passé]" in m.texte
    assert "mar. 06/10   22:45–07:00   Nuit" in m.texte and "mar. 06/10   22:45–07:00   Nuit   [passé]" not in m.texte
    assert "color:#999" in m.html


def test_jour_vide_affiche_un_tiret():
    assert "* mer. 07/10   Repos" in un_mail("suppression").texte


# --- Détail du changement --------------------------------------------------

def test_changement_de_poste_detaille():
    m = un_mail("changement-poste")
    assert section(m.texte, "CE QUI CHANGE") == [
        "- mer. 07/10 · Modifié",
        "    Poste : Nuit → Soir",
        "    Horaires : 22:45–07:00 (lendemain) → 14:45–22:45",
        "    Durée : 7h45 → 7h30",
    ]
    assert "* mer. 07/10   14:45–22:45   Soir" in m.texte


def test_repos_devient_travail():
    assert section(un_mail("repos-devient-travail").texte, "CE QUI CHANGE") == [
        "- jeu. 08/10 · Ajouté",
        "    Repos → Matin 07:00–15:45",
    ]


def test_horaires_seuls_pas_de_ligne_poste():
    assert section(un_mail("horaires-seuls").texte, "CE QUI CHANGE") == [
        "- dim. 11/10 · Modifié",
        "    Horaires : 07:00–15:45 → 08:00–16:45",
    ]


def test_suppression():
    assert section(un_mail("suppression").texte, "CE QUI CHANGE") == [
        "- mer. 07/10 · Annulé",
        "    Nuit 22:45–07:00 → Repos",
    ]


def test_ajout_dans_semaine_connue():
    assert section(un_mail("ajout-semaine-connue").texte, "CE QUI CHANGE") == [
        "- mer. 07/10 · Ajouté",
        "    Repos → Soir 14:45–22:45",
    ]


def test_plusieurs_changements_dans_l_ordre_des_jours():
    titres = [l for l in section(un_mail("plusieurs-changements").texte, "CE QUI CHANGE") if l.startswith("-")]
    assert titres == ["- mer. 07/10 · Modifié", "- jeu. 08/10 · Ajouté", "- sam. 10/10 · Ajouté"]
    marques = [l[2:12] for l in lignes_semaine(un_mail("plusieurs-changements").texte) if l.startswith("*")]
    assert marques == ["mer. 07/10", "jeu. 08/10", "sam. 10/10"]


# --- HTML ------------------------------------------------------------------

class _Texte(HTMLParser):
    def __init__(self):
        super().__init__()
        self.morceaux, self.barre = [], []
        self._dans_s = False

    def handle_starttag(self, tag, attrs):
        self._dans_s |= tag == "s"

    def handle_endtag(self, tag):
        if tag == "s":
            self._dans_s = False

    def handle_data(self, data):
        self.morceaux.append(data)
        if self._dans_s:
            self.barre.append(data)


def lire_html(m):
    p = _Texte()
    p.feed(m.html)
    return " ".join(p.morceaux), p.barre


def test_html_bloc_changement_et_ancien_barre():
    texte, barre = lire_html(un_mail("changement-poste"))
    assert "CE QUI CHANGE" in texte and "Poste : Nuit → Soir" in texte
    assert [b for b in barre if b != "barré"] == ["Nuit 22:45–07:00"]  # « barré » : la légende
    assert texte.count("Modifié") == 2  # résumé + ligne du tableau


def test_html_une_seule_ligne_surlignee_par_jour_change():
    m = un_mail("plusieurs-changements")
    assert m.html.count("<tr style='background:#fff4cc") == 3


@pytest.mark.parametrize("nom", list(SC))
def test_html_bien_forme(nom):
    for m in SC[nom].mails(CFG):
        assert m.html.count("<tr") == m.html.count("</tr>") == 7
        assert m.html.count("<table") == m.html.count("</table>") == 1


def test_html_echappe_les_intitules():
    from planning_relay.comparaison import comparer
    from planning_relay.mail import mails
    from planning_relay.modeles import Creneau

    j = date(2026, 10, 7)
    c = Creneau(j, "X", intitule="<script>alert(1)</script>")
    [m] = mails(CFG, {j: c}, {}, comparer({}, {j: c}), AUJOURDHUI)
    assert "<script>" not in m.html and "&lt;script&gt;" in m.html


# --- Aperçu ----------------------------------------------------------------

def test_apercu_ecrit_un_fichier_par_mail(tmp_path):
    from planning_relay.apercu import ecrire

    fichiers = ecrire(tmp_path, CFG)
    nb_mails = sum(len(s.mails(CFG)) for s in scenarios())
    assert len(fichiers) == nb_mails + 1
    assert (tmp_path / "index.html").read_text(encoding="utf-8").count("<section") == nb_mails
    assert (tmp_path / "changement-poste-1.txt").read_text(encoding="utf-8").startswith("Objet : Planning de Charlène")


def test_repos_jamais_reportes_dans_le_planning():
    m = un_mail("publiee")
    assert "jeu. 08/10   Repos" in m.texte and "ven. 09/10   Repos" in m.texte
    assert " R " not in m.texte and "RF" not in m.texte


def test_poste_par_heure_de_debut():
    from planning_relay.apercu import matin, night, soir
    from planning_relay.mail import poste

    j = date(2026, 10, 7)
    assert [poste(matin(j)), poste(soir(j)), poste(night(j))] == ["Matin", "Soir", "Nuit"]


def test_legende_postes_de_la_semaine_et_reperes():
    m = un_mail("changement-poste")
    texte, _ = lire_html(m)
    assert "Matin" in texte and "Soir" in texte and "Nuit" in texte
    assert "jour qui a changé" in texte and "ancien créneau" in texte and "jour passé" in texte
    assert "#3b4cc0" in m.html and "#ef7d1a" in m.html and "#2e9e4f" in m.html
    assert "(07H)" not in texte and "(SOIR)" not in texte


def test_legende_publiee_sans_reperes_de_changement():
    texte, _ = lire_html(un_mail("publiee"))
    assert "jour qui a changé" not in texte


@pytest.mark.parametrize("horaires, attendu", [
    ("07:00 - 15:45", "Matin"),
    ("06:00 - 14:00", "Matin"),
    ("08:00 - 16:30", "Matin"),
    ("14:45 - 22:45", "Soir"),
    ("16:00 - 23:30", "Soir"),
    ("13:00 - 21:00", "Soir"),
    ("22:45 - 07:00", "Nuit"),
    ("21:30 - 05:30", "Nuit"),
    ("23:30 - 07:30", "Nuit"),
    ("00:00 - 07:00", "Nuit"),
])
def test_poste_le_plus_proche_quand_les_horaires_varient(horaires, attendu):
    from planning_relay.mail import poste
    from planning_relay.modeles import creneau_depuis_cellule

    assert poste(creneau_depuis_cellule(date(2026, 10, 7), "X", horaires)) == attendu


@pytest.mark.parametrize("nom", list(SC))
def test_ni_codes_silae_ni_intitules_dans_le_mail(nom):
    for m in SC[nom].mails(CFG):
        texte_html, _ = lire_html(m)
        for contenu in (m.texte, texte_html):
            for interdit in ("NIGHT", "SOIR", "07H", "RECEP", "Semaine ", "MATIN"):
                assert interdit not in contenu.replace("Semaine 4", ""), (interdit, contenu)


def test_ligne_carre_horaires_puis_poste_dessous():
    m = un_mail("publiee")
    assert "<strong>22:45–07:00</strong><br><span style='color:#666;font-size:13px;margin-left:16px'>Nuit</span>" in m.html
