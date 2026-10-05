"""Mail d'alerte après 3 échecs consécutifs, et mail de retour à la normale.

Le compteur vit dans de petits fichiers du volume /data : un nombre et la date
du premier échec de la série, jamais de planning ni d'identifiant.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from .config import Config
from .diagnostic import Diagnostic

SEUIL = 3


class Compteur:
    def __init__(self, dossier: str):
        self.fichier = Path(dossier) / "echecs"
        self.fichier_debut = Path(dossier) / "premier_echec"

    def lire(self) -> int:
        try:
            return int(self.fichier.read_text().strip() or 0)
        except (FileNotFoundError, ValueError):
            return 0

    def depuis(self) -> datetime | None:
        try:
            return datetime.fromisoformat(self.fichier_debut.read_text().strip())
        except (FileNotFoundError, ValueError):
            return None

    def _ecrire(self, n: int) -> None:
        self.fichier.parent.mkdir(parents=True, exist_ok=True)
        self.fichier.write_text(str(n))

    def echec(self, maintenant: datetime | None = None) -> int:
        n = self.lire() + 1
        if n == 1:
            self.fichier_debut.parent.mkdir(parents=True, exist_ok=True)
            self.fichier_debut.write_text((maintenant or datetime.now()).isoformat())
        self._ecrire(n)
        return n

    def succes(self) -> None:
        if self.lire():
            self._ecrire(0)
        self.fichier_debut.unlink(missing_ok=True)


def doit_alerter(echecs_consecutifs: int) -> bool:
    # Une seule alerte par série d'échecs : au 3e, pas aux suivants.
    return echecs_consecutifs == SEUIL


def _quand(d: datetime | None) -> str:
    return f"le {d:%d/%m à %H:%M}" if d else "depuis peu"


STYLE = "font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-size:15px;color:#222;max-width:520px"


def mail_alerte(cfg: Config, diag: Diagnostic, n: int, depuis: datetime | None) -> tuple[str, str, str]:
    objet = f"⚠ planning-relay en panne : {diag.probleme.rstrip('.')}"
    lignes = [
        ("Problème", diag.probleme),
        ("Cause probable", diag.cause),
        ("À faire", diag.action),
    ]
    contexte = [
        f"Étape en échec : {diag.etape}",
        f"Échecs d'affilée : {n} (premier échec {_quand(depuis)})",
        f"Détail technique : {diag.detail}",
    ]
    rassurant = (f"En attendant, l'agenda garde les derniers créneaux connus de {cfg.personne}, "
                 "mais ses changements de planning ne sont plus repris. Le script réessaie à chaque heure "
                 "et t'enverra un mail quand ça refonctionne. Pas d'autre alerte d'ici là.")

    texte = "\n".join(
        [f"La synchro du planning de {cfg.personne} échoue depuis {n} passages.", ""]
        + [f"{titre.upper()}\n  {valeur}\n" for titre, valeur in lignes]
        + contexte + ["", rassurant, "", "Journal : docker logs planning-relay --tail 100 (sur rp-meliodas)"]
    )
    blocs = "".join(
        f"<div style='margin:0 0 12px'><div style='font-size:12px;font-weight:700;letter-spacing:.04em;color:#8a1c1c'>"
        f"{escape(titre.upper())}</div><div>{escape(valeur)}</div></div>"
        for titre, valeur in lignes
    )
    html = (
        f"<div style='{STYLE}'>"
        f"<h2 style='font-size:18px;margin:0 0 4px'>planning-relay en panne</h2>"
        f"<p style='margin:0 0 14px;color:#666'>La synchro du planning de {escape(cfg.personne)} échoue depuis {n} passages.</p>"
        f"<div style='background:#fdecea;border-left:4px solid #b42318;padding:12px 16px;margin:0 0 14px'>{blocs}</div>"
        "<table style='font-size:13px;color:#555;border-collapse:collapse;margin:0 0 14px'>"
        + "".join(
            f"<tr><td style='padding:2px 12px 2px 0;white-space:nowrap'>{escape(k)}</td><td>{escape(v)}</td></tr>"
            for k, v in (c.split(" : ", 1) for c in contexte)
        )
        + "</table>"
        f"<p style='margin:0 0 8px;font-size:14px'>{escape(rassurant)}</p>"
        "<p style='margin:0;font-size:13px;color:#888'>Journal : <code>docker logs planning-relay --tail 100</code> sur rp-meliodas</p>"
        "</div>"
    )
    return objet, texte, html


def mail_retabli(cfg: Config, n: int, depuis: datetime | None, maintenant: datetime) -> tuple[str, str, str]:
    objet = "✓ planning-relay refonctionne"
    texte = (f"La synchro du planning de {cfg.personne} refonctionne depuis le passage de {maintenant:%H:%M}, "
             f"après {n} échecs (premier échec {_quand(depuis)}).\n\n"
             "Les changements de planning survenus pendant la panne ont été repris à ce passage.")
    html = (
        f"<div style='{STYLE}'>"
        "<h2 style='font-size:18px;margin:0 0 4px'>planning-relay refonctionne</h2>"
        f"<div style='background:#e8f5e9;border-left:4px solid #1a7f37;padding:12px 16px;margin:8px 0 0'>"
        f"La synchro du planning de {escape(cfg.personne)} refonctionne depuis le passage de {maintenant:%H:%M}, "
        f"après {n} échecs (premier échec {_quand(depuis)}).<br><br>"
        "Les changements de planning survenus pendant la panne ont été repris à ce passage.</div></div>"
    )
    return objet, texte, html
