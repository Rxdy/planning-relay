import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from planning_relay.alerte import Compteur
from planning_relay.config import Config
from planning_relay.service import prochaine_heure, sante, tourner

PARIS = ZoneInfo("Europe/Paris")


def cfg(tmp_path):
    return Config.depuis_env({"STATE_DIR": str(tmp_path)})


def t(h, m=0, s=0, jour=5):
    return datetime(2026, 10, jour, h, m, s, tzinfo=PARIS)


def test_prochaine_heure_pile():
    assert prochaine_heure(t(15, 26, 40)) == t(16)
    assert prochaine_heure(t(16)) == t(17)  # pile à l'heure : la suivante
    assert prochaine_heure(t(23, 59, 59)) == t(0, jour=6)


class Horloge:
    """Avance le temps à chaque attente au lieu de dormir."""

    def __init__(self, depart):
        self.maintenant = depart

    def __call__(self):
        return self.maintenant


class Arret(threading.Event):
    def __init__(self, horloge, passages_max, passages):
        super().__init__()
        self.horloge, self.max, self.passages = horloge, passages_max, passages

    def wait(self, timeout=None):
        self.horloge.maintenant += timedelta(seconds=timeout)
        return self.is_set()


def test_un_passage_a_chaque_heure_pile_jamais_au_demarrage(tmp_path):
    horloge = Horloge(t(15, 26, 40))
    heures = []
    arret = Arret(horloge, 3, heures)

    def passage(_cfg):
        heures.append(horloge().strftime("%H:%M:%S"))
        if len(heures) == 3:
            arret.set()

    tourner(cfg(tmp_path), passage, arret, horloge)
    assert heures == ["16:00:00", "17:00:00", "18:00:00"]
    assert (tmp_path / "dernier_passage").read_text().startswith("2026-10-05T18:00")


def test_arret_pendant_l_attente_sans_passage(tmp_path):
    horloge = Horloge(t(15, 26))
    arret = Arret(horloge, 0, [])
    arret.set()
    appels = []
    tourner(cfg(tmp_path), lambda c: appels.append(1), arret, horloge)
    assert appels == []


def test_une_erreur_n_arrete_pas_le_service(tmp_path):
    horloge = Horloge(t(15, 59))
    n = []
    arret = Arret(horloge, 2, n)

    def passage(_cfg):
        n.append(1)
        if len(n) == 2:
            arret.set()
        raise RuntimeError("imprévu")

    tourner(cfg(tmp_path), passage, arret, horloge)
    assert len(n) == 2


def test_sante(tmp_path):
    c = cfg(tmp_path)
    assert sante(c, t(15))[0]  # juste démarré, aucun passage encore
    (tmp_path / "dernier_passage").write_text(t(15).isoformat())
    assert sante(c, t(16, 30))[0]
    assert not sante(c, t(17, 31))[0]  # plus de 2 h 30 sans passage : bloqué
    compteur = Compteur(str(tmp_path))
    for _ in range(3):
        compteur.echec()
    ok, message = sante(c, t(15, 10))
    assert not ok and "3 échecs" in message
    compteur.succes()
    assert sante(c, t(15, 10))[0]
