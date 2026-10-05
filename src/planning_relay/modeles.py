from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal


@dataclass(frozen=True)
class Creneau:
    """Un créneau de Charlène : au plus un par jour, donc la date sert de clé."""

    jour: date
    code: str
    debut: time | None = None
    fin: time | None = None
    duree: str | None = None  # "7h30", tel qu'affiché
    pause: str | None = None  # "30 min", tel qu'affiché

    @property
    def journee_entiere(self) -> bool:
        return self.debut is None or self.fin is None

    @property
    def fin_le_lendemain(self) -> bool:
        # NIGHT : 22:45 - 07:00
        return not self.journee_entiere and self.fin <= self.debut

    def bornes(self) -> tuple[datetime, datetime]:
        assert not self.journee_entiere
        debut = datetime.combine(self.jour, self.debut)
        fin_jour = self.jour + timedelta(days=1) if self.fin_le_lendemain else self.jour
        return debut, datetime.combine(fin_jour, self.fin)

    def empreinte(self) -> tuple:
        """Ce qui compte pour décider qu'un créneau a été modifié."""
        return (self.code, self.debut, self.fin)

    def libelle(self) -> str:
        if self.journee_entiere:
            return self.code
        return f"{self.code} {self.debut:%H:%M}–{self.fin:%H:%M}"


_HORAIRES = re.compile(r"(\d{1,2})[:hH](\d{2})\s*-\s*(\d{1,2})[:hH](\d{2})")
_DUREE = re.compile(r"\((\d+h\d*)\)")
_PAUSE = re.compile(r"pause\s*(\d+\s*min)", re.IGNORECASE)


def creneau_depuis_cellule(jour: date, code: str, texte: str = "") -> Creneau:
    """Normalise une cellule telle qu'affichée : « 14:45 - 22:45 (7h30), pause 30 min ».

    Les codes ne sont pas une liste fermée : sans horaires lisibles, le créneau
    devient une journée entière (R, RF, ou tout code inconnu).
    """
    code = code.strip().upper()
    m = _HORAIRES.search(texte or "")
    if not m:
        return Creneau(jour=jour, code=code)
    h1, m1, h2, m2 = (int(g) for g in m.groups())
    duree = _DUREE.search(texte)
    pause = _PAUSE.search(texte)
    return Creneau(
        jour=jour,
        code=code,
        debut=time(h1, m1),
        fin=time(h2, m2),
        duree=duree.group(1) if duree else None,
        pause=pause.group(1) if pause else None,
    )


@dataclass(frozen=True)
class Changement:
    type: Literal["ajout", "modif", "suppr"]
    jour: date
    avant: Creneau | None
    apres: Creneau | None
