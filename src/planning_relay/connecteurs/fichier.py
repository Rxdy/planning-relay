"""Connecteur de test : lit un planning dans un fichier JSON.

Sert à éprouver F2 à F4 sur un agenda de test avant que le connecteur Silae
soit prêt. Format :

    [{"jour": "2026-10-06", "code": "SOIR", "texte": "14:45 - 22:45 (7h30), pause 30 min"},
     {"jour": "2026-10-07", "code": "R"}]
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ..modeles import Creneau, creneau_depuis_cellule


class ConnecteurFichier:
    def __init__(self, chemin: str):
        self.chemin = Path(chemin)

    def recuperer(self, debut: date, fin: date) -> list[Creneau]:
        lignes = json.loads(self.chemin.read_text(encoding="utf-8"))
        creneaux = [
            creneau_depuis_cellule(date.fromisoformat(l["jour"]), l["code"], l.get("texte", ""))
            for l in lignes
        ]
        return [c for c in creneaux if debut <= c.jour <= fin]
