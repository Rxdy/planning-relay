from __future__ import annotations

import unicodedata


def _normaliser(texte: str) -> str:
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return " ".join(sans_accents.casefold().split())


def correspond(libelle_ligne: str, person_match: str) -> bool:
    """Repère la ligne de Charlène par PERSON_MATCH.

    PERSON_MATCH est soit un identifiant agent (comparaison exacte), soit son
    nom tel qu'affiché (« Charlène DUPONT ») : casse, accents et espaces ignorés.
    """
    if not person_match:
        raise ValueError("PERSON_MATCH est vide : impossible de repérer la ligne")
    return _normaliser(libelle_ligne) == _normaliser(person_match)
