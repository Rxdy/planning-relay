from __future__ import annotations

import argparse
import logging
import sys

from .config import Config

log = logging.getLogger("planning_relay")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="planning-relay")
    sous = parser.add_subparsers(dest="commande", required=True)
    sous.add_parser("service", help="Conteneur permanent : un passage à chaque heure pile")
    sous.add_parser("sync", help="Un passage : lire, comparer, prévenir, publier")
    sous.add_parser("sante", help="Santé du service, pour le HEALTHCHECK Docker")
    ap = sous.add_parser("apercu", help="Écrit les mails des scénarios types en HTML et texte")
    ap.add_argument("--dossier", default="apercus")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%d/%m %H:%M:%S")
    # Une ligne par requête HTTP ne sert à rien au quotidien ; les erreurs restent
    for bavard in ("httpx", "httpcore", "googleapiclient"):
        logging.getLogger(bavard).setLevel(logging.WARNING)
    cfg = Config.depuis_env()

    if args.commande == "service":
        from .service import lancer

        return lancer(cfg, synchro)

    if args.commande == "sante":
        from .service import sante

        ok, message = sante(cfg)
        print(message)
        return 0 if ok else 1

    if args.commande == "sync":
        return synchro(cfg)

    if args.commande == "apercu":
        from pathlib import Path

        from .apercu import ecrire

        index, *_ = ecrire(Path(args.dossier), cfg)
        print(f"Aperçus écrits ; ouvrir {index}")
        return 0



def synchro(cfg: Config) -> int:
    from .agenda import Agenda
    from .alerte import Compteur, doit_alerter
    from .connecteurs import creer_connecteur
    from .mail import Messagerie
    from .synchro import passage

    compteur = Compteur(cfg.dossier_etat)
    try:
        passage(cfg, creer_connecteur(cfg), Agenda(cfg), Messagerie(cfg))
    except Exception as e:
        n = compteur.echec()
        # Ne logguer que le type : un message d'erreur peut contenir des données.
        log.error("Passage en échec (%s), %d échec(s) d'affilée", type(e).__name__, n)
        if doit_alerter(n):
            Messagerie(cfg).envoyer(
                f"Planning de {cfg.personne} : synchro en panne",
                f"La synchro du planning a échoué {n} fois de suite (dernière erreur : {type(e).__name__}).\n\n"
                "Causes probables : identifiants expirés, site modifié, agenda inaccessible.\n"
                "Journal sur le Pi : journalctl -u planning-relay -n 100",
            )
            log.info("Alerte envoyée")
        return 1
    compteur.succes()
    return 0


if __name__ == "__main__":
    sys.exit(main())
