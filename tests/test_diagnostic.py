import json
import smtplib
from types import SimpleNamespace

import httpx
import pytest

from planning_relay.connecteurs import LigneIntrouvable
from planning_relay.connecteurs.silae import IdentifiantsRefuses, SessionPerdue, SiteModifie
from planning_relay.diagnostic import diagnostiquer
from planning_relay.synchro import etape


def avec_etape(e, nom):
    try:
        with etape(nom):
            raise e
    except Exception as x:
        return x


class HttpError(Exception):  # même nom que googleapiclient.errors.HttpError
    def __init__(self, status):
        self.resp = SimpleNamespace(status=status)


class RefreshError(Exception):
    pass


def http_status(code):
    req = httpx.Request("GET", "https://sirh.software/planning")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, request=req))


@pytest.mark.parametrize("erreur, etape_attendue, mot_cle", [
    (IdentifiantsRefuses("x"), "Silae", "PLATFORM_PASSWORD"),
    (SiteModifie("x"), "Silae", "connecteur Silae est à adapter"),
    (SessionPerdue("x"), "Silae", "prochain passage"),
    (LigneIntrouvable("x"), "Silae", "PERSON_MATCH"),
    (http_status(503), "Silae", "maintenance"),
    (http_status(404), "Silae", "a peut-être changé"),
    (httpx.ConnectError("x"), "Silae", "injoignable"),
    (json.JSONDecodeError("x", "", 0), "Silae", "format inattendu"),
    (RefreshError(), "Google Agenda", "GOOGLE_SA_JSON"),
    (HttpError(403), "Google Agenda", "Repartager"),
    (HttpError(500), "Google Agenda", "passagère"),
    (smtplib.SMTPAuthenticationError(535, b"x"), "Gmail", "SMTP_PASSWORD"),
    (ValueError("PERSON_MATCH doit être le matricule"), "Configuration", ".env"),
    (RuntimeError("?"), "Inconnue", "docker logs"),
])
def test_diagnostic(erreur, etape_attendue, mot_cle):
    d = diagnostiquer(erreur)
    assert d.etape == etape_attendue
    assert mot_cle in f"{d.probleme} {d.cause} {d.action}"


@pytest.mark.parametrize("nom, attendu", [
    ("Silae", "sirh.software est injoignable."),
    ("Google Agenda", "Google Agenda est injoignable."),
    ("Gmail", "L'envoi du mail de planning a échoué."),
])
def test_coupure_reseau_attribuee_a_la_bonne_etape(nom, attendu):
    assert diagnostiquer(avec_etape(TimeoutError(), nom)).probleme == attendu


def test_detail_sans_texte_brut_d_erreur_exterieure():
    d = diagnostiquer(avec_etape(OSError("secret dans le message"), "Silae"))
    assert "secret" not in d.detail
