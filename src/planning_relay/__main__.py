from __future__ import annotations

import argparse
import logging
import os
import sys

from .config import Config

log = logging.getLogger("planning_relay")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="planning-relay")
    sous = parser.add_subparsers(dest="commande", required=True)
    sous.add_parser("sync", help="Un passage : lire, comparer, prévenir, publier")
    sous.add_parser("alerte", help="Mail d'alerte si c'est le 3e échec consécutif")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = Config.depuis_env()

    if args.commande == "sync":
        from .agenda import Agenda
        from .connecteurs import creer_connecteur
        from .mail import Messagerie
        from .synchro import passage

        passage(cfg, creer_connecteur(cfg), Agenda(cfg), Messagerie(cfg))
        return 0

    from .alerte import conclusions_precedentes, doit_alerter
    from .mail import Messagerie

    if doit_alerter(conclusions_precedentes()):
        lien = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY', '')}/actions"
        Messagerie(cfg).envoyer(
            f"Planning de {cfg.personne} : synchro en panne",
            "La synchro du planning a échoué 3 fois de suite.\n\n"
            "Causes probables : identifiants expirés, site modifié, ligne introuvable.\n"
            f"Détails : {lien}",
        )
        log.info("Alerte envoyée")
    return 0


if __name__ == "__main__":
    sys.exit(main())
