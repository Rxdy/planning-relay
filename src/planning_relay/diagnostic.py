"""Traduit une erreur de passage en explication lisible pour le mail d'alerte.

Le détail technique ne reprend jamais le texte brut d'une erreur extérieure
(il pourrait contenir des données) : seulement le type, un code HTTP, ou nos
propres messages.
"""

from __future__ import annotations

import json
import smtplib
from dataclasses import dataclass

import httpx

from .connecteurs import LigneIntrouvable
from .connecteurs.silae import IdentifiantsRefuses, SessionPerdue, SiteModifie


@dataclass(frozen=True)
class Diagnostic:
    etape: str  # Silae, Google Agenda, Gmail, Configuration, Inconnue
    probleme: str
    cause: str
    action: str
    detail: str


def _code_google(e: Exception) -> int | None:
    resp = getattr(e, "resp", None)
    try:
        return int(getattr(resp, "status", None))
    except (TypeError, ValueError):
        return None


def diagnostiquer(e: Exception) -> Diagnostic:
    nom = type(e).__name__

    # --- sirh.software (Silae) ---
    if isinstance(e, IdentifiantsRefuses):
        return Diagnostic("Silae", "Silae refuse la connexion de Charlène.",
                          "Son mot de passe sirh.software a probablement changé ou expiré.",
                          "Mettre le nouveau mot de passe dans PLATFORM_PASSWORD (~/planning-relay/.env), puis relancer le conteneur.",
                          f"{nom} : {e}")
    if isinstance(e, SiteModifie):
        return Diagnostic("Silae", "La page de connexion de sirh.software a changé.",
                          "Silae a modifié son site : le script ne trouve plus le formulaire attendu.",
                          "Le connecteur Silae est à adapter dans le code.",
                          f"{nom} : {e}")
    if isinstance(e, SessionPerdue):
        return Diagnostic("Silae", "Silae a fermé la session pendant la lecture du planning.",
                          "Souvent passager (maintenance du site) ; si ça dure, Silae a changé son fonctionnement.",
                          "Attendre le prochain passage ; si l'alerte persiste, regarder le connecteur.",
                          f"{nom} : {e}")
    if isinstance(e, LigneIntrouvable):
        return Diagnostic("Silae", "Le planning de Charlène est introuvable dans les données Silae.",
                          "Son matricule a peut-être changé (changement de contrat ou de service).",
                          "Vérifier le matricule dans « Ma fiche » sur sirh.software et le mettre dans PERSON_MATCH.",
                          f"{nom} : {e}")
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        return Diagnostic("Silae", f"sirh.software répond par une erreur {code}.",
                          "Site en panne ou en maintenance." if code >= 500 else "Le site refuse la requête : il a peut-être changé.",
                          "Attendre ; si l'alerte persiste plusieurs heures, regarder le connecteur.",
                          f"{nom} : HTTP {code}")
    if isinstance(e, httpx.TransportError) or (isinstance(e, OSError) and getattr(e, "etape", "") == "Silae"):
        return Diagnostic("Silae", "sirh.software est injoignable.",
                          "Site hors ligne, ou connexion internet du Pi coupée.",
                          "Vérifier que le site s'ouvre et que le Pi a internet.",
                          nom)
    if isinstance(e, (json.JSONDecodeError, KeyError)):
        return Diagnostic("Silae", "Silae renvoie des données dans un format inattendu.",
                          "Silae a modifié son application.",
                          "Le connecteur Silae est à adapter dans le code.",
                          nom)

    # --- Google Agenda ---
    if nom == "RefreshError":
        return Diagnostic("Google Agenda", "Google refuse la clé du compte de service.",
                          "La clé a été supprimée ou désactivée dans la console Google Cloud.",
                          "Créer une nouvelle clé pour planning-relay et la mettre dans GOOGLE_SA_JSON.",
                          nom)
    if nom == "HttpError":
        code = _code_google(e)
        if code in (403, 404):
            return Diagnostic("Google Agenda", "Le script n'a plus accès à l'agenda d'Abview.",
                              "Le partage avec planning-relay@project-abview.iam.gserviceaccount.com a été retiré ou modifié.",
                              "Repartager l'agenda avec ce compte, en « Apporter des modifications aux événements ».",
                              f"{nom} : HTTP {code}")
        return Diagnostic("Google Agenda", f"Google Agenda répond par une erreur {code}.",
                          "Panne passagère côté Google, en général.",
                          "Attendre le prochain passage.",
                          f"{nom} : HTTP {code}")

    # --- Gmail ---
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return Diagnostic("Gmail", "Gmail refuse l'envoi des mails de planning.",
                          "Le mot de passe d'application a été révoqué.",
                          "Créer un nouveau mot de passe d'application et le mettre dans SMTP_PASSWORD.",
                          nom)
    if isinstance(e, OSError) and getattr(e, "etape", "") == "Google Agenda":
        return Diagnostic("Google Agenda", "Google Agenda est injoignable.",
                          "Connexion internet du Pi coupée, ou panne passagère côté Google.",
                          "Attendre le prochain passage ; vérifier que le Pi a internet si ça dure.",
                          nom)
    if isinstance(e, smtplib.SMTPException) or (isinstance(e, OSError) and getattr(e, "etape", "") == "Gmail"):
        return Diagnostic("Gmail", "L'envoi du mail de planning a échoué.",
                          "Serveur Gmail injoignable ou connexion du Pi coupée.",
                          "Attendre le prochain passage.",
                          nom)

    # --- Configuration ---
    if isinstance(e, ValueError):
        return Diagnostic("Configuration", "La configuration du script est incomplète ou invalide.",
                          str(e), "Vérifier ~/planning-relay/.env.", f"{nom} : {e}")

    return Diagnostic("Inconnue", "Erreur inattendue.",
                      "Cas non prévu par le script.",
                      "Regarder le journal : docker logs planning-relay --tail 100",
                      nom)
