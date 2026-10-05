"""Mail d'alerte après 3 échecs consécutifs.

Le compteur vit dans un petit fichier du volume /data : il ne contient
qu'un nombre, jamais de planning ni d'identifiant.
"""

from __future__ import annotations

from pathlib import Path

SEUIL = 3


class Compteur:
    def __init__(self, dossier: str):
        self.fichier = Path(dossier) / "echecs"

    def lire(self) -> int:
        try:
            return int(self.fichier.read_text().strip() or 0)
        except (FileNotFoundError, ValueError):
            return 0

    def _ecrire(self, n: int) -> None:
        self.fichier.parent.mkdir(parents=True, exist_ok=True)
        self.fichier.write_text(str(n))

    def echec(self) -> int:
        n = self.lire() + 1
        self._ecrire(n)
        return n

    def succes(self) -> None:
        if self.lire():
            self._ecrire(0)


def doit_alerter(echecs_consecutifs: int) -> bool:
    # Une seule alerte par série d'échecs : au 3e, pas aux suivants.
    return echecs_consecutifs == SEUIL
