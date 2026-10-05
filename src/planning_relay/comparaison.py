from __future__ import annotations

from datetime import date

from .modeles import Changement, Creneau


def comparer(connus: dict[date, Creneau], lus: dict[date, Creneau]) -> list[Changement]:
    """F2 : écarts entre l'agenda (dernier planning connu) et la plateforme.

    Les deux dictionnaires doivent déjà être limités à la même fenêtre
    (aujourd'hui → fin de la 4e semaine) : un jour passé n'est pas un changement.
    """
    changements = []
    for jour in sorted(connus.keys() | lus.keys()):
        avant, apres = connus.get(jour), lus.get(jour)
        if avant is None:
            changements.append(Changement("ajout", jour, None, apres))
        elif apres is None:
            changements.append(Changement("suppr", jour, avant, None))
        elif avant.empreinte() != apres.empreinte():
            changements.append(Changement("modif", jour, avant, apres))
    return changements
