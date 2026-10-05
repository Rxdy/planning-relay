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
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from .agenda import Agenda
    from .alerte import SEUIL, Compteur, doit_alerter, mail_alerte, mail_retabli
    from .connecteurs import creer_connecteur
    from .diagnostic import diagnostiquer
    from .mail import Messagerie
    from .synchro import passage

    maintenant = datetime.now(ZoneInfo(cfg.fuseau))
    compteur = Compteur(cfg.dossier_etat)
    try:
        passage(cfg, creer_connecteur(cfg), Agenda(cfg), Messagerie(cfg))
    except Exception as e:
        n = compteur.echec(maintenant)
        diag = diagnostiquer(e)
        # Ne logguer que le diagnostic : le texte brut d'une erreur peut contenir des données.
        log.error("Passage en échec (%s : %s), %d échec(s) d'affilée", diag.etape, diag.detail, n)
        if doit_alerter(n):
            try:
                objet, texte, html = mail_alerte(cfg, diag, n, compteur.depuis())
                Messagerie(cfg).envoyer(objet, texte, html, cfg.alerte_to)
                log.info("Alerte envoyée")
            except Exception as e2:
                log.error("Alerte impossible à envoyer (%s)", type(e2).__name__)
        return 1

    n = compteur.lire()
    if n >= SEUIL:
        # Une alerte était partie : on prévient que c'est reparti.
        try:
            objet, texte, html = mail_retabli(cfg, n, compteur.depuis(), maintenant)
            Messagerie(cfg).envoyer(objet, texte, html, cfg.alerte_to)
        except Exception as e2:
            log.error("Mail de rétablissement impossible à envoyer (%s)", type(e2).__name__)
    compteur.succes()
    return 0


if __name__ == "__main__":
    sys.exit(main())
