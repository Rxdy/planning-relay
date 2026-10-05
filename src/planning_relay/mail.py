"""F3 : un seul mail récapitulatif par exécution, et le mail d'alerte."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .config import Config
from .modeles import Changement, Creneau

JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
TYPES = {"ajout": "Ajouté", "modif": "Modifié", "suppr": "Supprimé"}


def _semaines(changements: list[Changement]) -> str:
    nums = sorted({ch.jour.isocalendar().week for ch in changements})
    return f"sem. {nums[0]}" if len(nums) == 1 else "sem. " + ", ".join(map(str, nums))


def objet(cfg: Config, changements: list[Changement]) -> str:
    n = len(changements)
    pluriel = "s" if n > 1 else ""
    return f"Planning de {cfg.personne} : {n} changement{pluriel} ({_semaines(changements)})"


def _texte(c: Creneau | None) -> str:
    return c.libelle() if c else "—"


def corps(cfg: Config, changements: list[Changement]) -> str:
    lignes = [f"Le planning de {cfg.personne} a changé :", ""]
    for ch in changements:
        jour = f"{JOURS[ch.jour.weekday()]} {ch.jour:%d/%m}"
        lignes.append(f"{TYPES[ch.type]:<9} {jour} : {_texte(ch.avant)}  →  {_texte(ch.apres)}")
    lignes += ["", "L'affichage familial est mis à jour."]
    return "\n".join(lignes)


class Messagerie:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def envoyer(self, sujet: str, texte: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.cfg.smtp_user
        msg["To"] = ", ".join(self.cfg.mail_to)
        msg["Subject"] = sujet
        msg.set_content(texte)
        with smtplib.SMTP_SSL(self.cfg.smtp_host, self.cfg.smtp_port, timeout=30) as smtp:
            smtp.login(self.cfg.smtp_user, self.cfg.smtp_password)
            smtp.send_message(msg)

    def recapitulatif(self, changements: list[Changement]) -> None:
        self.envoyer(objet(self.cfg, changements), corps(self.cfg, changements))
