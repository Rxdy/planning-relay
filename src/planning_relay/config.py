from __future__ import annotations

import os
from dataclasses import dataclass, field


def _liste(valeur: str) -> list[str]:
    return [v.strip() for v in valeur.split(",") if v.strip()]


@dataclass(frozen=True)
class Config:
    # Plateforme
    connecteur: str
    platform_user: str
    platform_password: str
    person_match: str
    fichier_planning: str
    # Personne suivie
    personne: str
    cle_synchro: str
    # Google Agenda
    google_sa_json: str
    calendar_id: str
    titre: str
    couleur: str | None
    codes_ignores: list[str] = field(default_factory=list)
    # Mail
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    mail_to: list[str] = field(default_factory=list)
    # Exécution
    semaines: int = 4
    essais: int = 3
    delai_essai: int = 300
    fuseau: str = "Europe/Paris"
    dry_run: bool = False

    @classmethod
    def depuis_env(cls, env: dict[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        g = lambda k, d="": env.get(k, d).strip()  # noqa: E731
        return cls(
            connecteur=g("CONNECTOR", "silae"),
            platform_user=g("PLATFORM_USER"),
            platform_password=g("PLATFORM_PASSWORD"),
            person_match=g("PERSON_MATCH"),
            fichier_planning=g("PLANNING_FILE"),
            personne=g("PERSON_NAME", "Charlène"),
            cle_synchro=g("SYNC_KEY", "charlene"),
            google_sa_json=g("GOOGLE_SA_JSON"),
            calendar_id=g("CALENDAR_ID"),
            titre=g("EVENT_TITLE", "{personne} — {code}"),
            couleur=g("EVENT_COLOR_ID") or None,
            codes_ignores=[c.upper() for c in _liste(g("SKIP_CODES"))],
            smtp_host=g("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=int(g("SMTP_PORT", "465")),
            smtp_user=g("SMTP_USER"),
            smtp_password=g("SMTP_PASSWORD"),
            mail_to=_liste(g("MAIL_TO")),
            semaines=int(g("WEEKS", "4")),
            essais=int(g("FETCH_ATTEMPTS", "3")),
            delai_essai=int(g("FETCH_RETRY_DELAY", "300")),
            dry_run=g("DRY_RUN").lower() in {"1", "true", "oui", "yes"},
        )
