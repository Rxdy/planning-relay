"""Scénarios de mail types, pour les tests et pour relire le rendu à l'œil.

    planning-relay apercu --dossier apercus/

écrit un fichier .html et un .txt par scénario, plus un index.html qui les
montre tous.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from html import escape
from pathlib import Path

from .comparaison import comparer
from .config import Config
from .mail import Mail, mails
from .modeles import Creneau

AUJOURDHUI = date(2026, 10, 6)  # mardi, semaine 41


def _d(jour: int, mois: int = 10) -> date:
    return date(2026, mois, jour)


def soir(j: date) -> Creneau:
    return Creneau(j, "SOIR", time(14, 45), time(22, 45), "7h30", "30 min", "RECEP SOIR")


def night(j: date) -> Creneau:
    return Creneau(j, "NIGHT", time(22, 45), time(7, 0), "7h45", "30 min", "Semaine")


def matin(j: date) -> Creneau:
    return Creneau(j, "07H", time(7, 0), time(15, 45), "8h15", "30 min", "MATIN")


def repos(j: date) -> Creneau:
    return Creneau(j, "R", intitule="Repos")


def recup(j: date) -> Creneau:
    return Creneau(j, "RF", intitule="Récup Férié")


# Semaine 41 telle que relevée le 5 octobre 2026
SEMAINE_41 = {
    _d(5): soir(_d(5)), _d(6): night(_d(6)), _d(7): night(_d(7)), _d(8): repos(_d(8)),
    _d(9): recup(_d(9)), _d(10): repos(_d(10)), _d(11): matin(_d(11)),
}
SEMAINE_42 = {
    _d(12): matin(_d(12)), _d(13): matin(_d(13)), _d(14): repos(_d(14)), _d(15): soir(_d(15)),
    _d(16): soir(_d(16)), _d(17): repos(_d(17)), _d(18): night(_d(18)),
}


@dataclass(frozen=True)
class Scenario:
    nom: str
    description: str
    avant: dict[date, Creneau]  # l'agenda avant le passage
    apres: dict[date, Creneau]  # la plateforme, depuis le lundi de la semaine en cours

    def mails(self, cfg: Config) -> list[Mail]:
        # Comme un passage : codes ignorés retirés, comparaison à partir d'aujourd'hui
        garder = lambda p: {j: c for j, c in p.items() if c.code not in cfg.codes_ignores}  # noqa: E731
        planning, avant = garder(self.apres), garder(self.avant)
        lus = {j: c for j, c in planning.items() if j >= AUJOURDHUI}
        connus = {j: c for j, c in avant.items() if j >= AUJOURDHUI}
        return mails(cfg, planning, connus, comparer(connus, lus), AUJOURDHUI)


def _avec(base: dict, **changes) -> dict:
    return {**base, **changes}


def scenarios() -> list[Scenario]:
    s41 = SEMAINE_41
    tout = {**SEMAINE_41, **SEMAINE_42}
    sans_mer = {j: c for j, c in s41.items() if j != _d(7)}
    return [
        Scenario("publiee", "Première synchro : la semaine en cours arrive d'un coup", {}, s41),
        Scenario("nouvelle-semaine", "La semaine 42 est publiée ; la 41 ne bouge pas", s41, tout),
        Scenario("changement-poste", "Mercredi : NIGHT devient SOIR (poste, horaires et durée)",
                 s41, {**s41, _d(7): soir(_d(7))}),
        Scenario("repos-devient-travail", "Jeudi : le repos devient un MATIN (ajout)", s41, {**s41, _d(8): matin(_d(8))}),
        Scenario("horaires-seuls", "Dimanche : même poste, horaires décalés d'une heure",
                 s41, {**s41, _d(11): Creneau(_d(11), "07H", time(8, 0), time(16, 45), "8h15", "30 min", "MATIN")}),
        Scenario("suppression", "Mercredi : la NIGHT est annulée, le jour devient repos", s41, sans_mer),
        Scenario("ajout-semaine-connue", "Mercredi était vide, un SOIR y est ajouté", sans_mer, {**sans_mer, _d(7): soir(_d(7))}),
        Scenario("plusieurs-changements", "Trois jours changent dans la même semaine",
                 s41, {**s41, _d(7): soir(_d(7)), _d(8): matin(_d(8)), _d(10): night(_d(10))}),
        Scenario("deux-semaines", "Un changement en 41 et un en 42 : deux mails",
                 tout, {**tout, _d(9): matin(_d(9)), _d(14): soir(_d(14))}),
    ]


def ecrire(dossier: Path, cfg: Config) -> list[Path]:
    dossier.mkdir(parents=True, exist_ok=True)
    blocs, fichiers = [], []
    for sc in scenarios():
        for i, m in enumerate(sc.mails(cfg), 1):
            base = dossier / f"{sc.nom}-{i}"
            base.with_suffix(".html").write_text(f"<meta charset=utf-8>{m.html}", encoding="utf-8")
            base.with_suffix(".txt").write_text(f"Objet : {m.objet}\n\n{m.texte}\n", encoding="utf-8")
            fichiers.append(base.with_suffix(".html"))
            blocs.append(
                "<section style='background:#fff;max-width:560px;padding:20px;margin:0 0 24px;border-radius:8px'>"
                f"<p style='font:12px monospace;color:#888;margin:0'>{escape(sc.nom)} — {escape(sc.description)}</p>"
                f"<p style='font:13px sans-serif;color:#555;margin:4px 0 16px'>Objet : <b>{escape(m.objet)}</b></p>"
                f"{m.html}</section>"
            )
    index = dossier / "index.html"
    index.write_text(
        "<meta charset=utf-8><title>Aperçu des mails</title>"
        "<body style='background:#f2f2f2;padding:24px;margin:0'>" + "".join(blocs),
        encoding="utf-8",
    )
    return [index, *fichiers]
