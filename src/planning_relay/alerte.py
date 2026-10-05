"""Mail d'alerte après 3 échecs consécutifs.

Il n'y a pas de fichier d'état : on demande à GitHub l'issue des passages
précédents. Appelé par le workflow quand le passage courant a échoué.
"""

from __future__ import annotations

import os

import httpx

SEUIL = 3


def echecs_consecutifs(conclusions_precedentes: list[str]) -> int:
    """Nombre d'échecs d'affilée, passage courant (en échec) compris."""
    n = 1
    for c in conclusions_precedentes:
        if c != "failure":
            break
        n += 1
    return n


def conclusions_precedentes() -> list[str]:
    depot = os.environ["GITHUB_REPOSITORY"]
    run_id = int(os.environ["GITHUB_RUN_ID"])
    workflow = os.environ["GITHUB_WORKFLOW_REF"].split("@")[0].rsplit("/", 1)[-1]
    reponse = httpx.get(
        f"https://api.github.com/repos/{depot}/actions/workflows/{workflow}/runs",
        params={"status": "completed", "per_page": SEUIL + 1},
        headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                 "Accept": "application/vnd.github+json"},
        timeout=30,
    )
    reponse.raise_for_status()
    runs = [r for r in reponse.json()["workflow_runs"] if r["id"] != run_id]
    return [r["conclusion"] for r in runs]


def doit_alerter(conclusions: list[str]) -> bool:
    # Une seule alerte par série d'échecs : au 3e, pas aux suivants.
    return echecs_consecutifs(conclusions) == SEUIL
