# planning-relay

Recopie toutes les heures un planning de travail (Silae RH Suite) dans le Google Agenda lu par l'affichage familial. Envoie un mail récapitulatif à chaque changement.

Un passage : lire la ligne de la personne suivie (les collègues sont écartés dès la lecture), comparer avec l'agenda, qui sert d'état de référence, envoyer un mail s'il y a des écarts, puis écrire dans l'agenda. Seuls les événements portant la propriété privée `planning_relay` sont touchés.

## Branches

`dev` → `staging` → `main`, toutes protégées : passage par une pull request et CI verte obligatoires. Chaque merge publie l'image `ghcr.io/rxdy/planning-relay` avec le tag `dev`, `staging` ou `latest`.

## Exécution

`.github/workflows/sync.yml` tourne à la minute 5 de chaque heure sur le Raspberry (runner auto-hébergé `self-hosted, linux, ARM64`). Les identifiants n'existent que dans les secrets GitHub et en mémoire pendant le passage. Un lancement manuel permet de choisir le tag de l'image et le mode `dry_run`.

| Nom | Type | Rôle |
|---|---|---|
| `PLATFORM_USER`, `PLATFORM_PASSWORD` | secret | Connexion à sirh.software |
| `GOOGLE_SA_JSON` | secret | Clé du compte de service (scope `calendar.events`) |
| `SMTP_USER`, `SMTP_PASSWORD` | secret | Expéditeur Gmail et mot de passe d'application |
| `PERSON_MATCH` | variable | Nom affiché ou matricule de la ligne à lire |
| `CALENDAR_ID` | variable | Agenda de l'affichage |
| `MAIL_TO` | variable | Destinataires, séparés par des virgules |
| `SKIP_CODES` | variable | Codes à ne pas afficher, par exemple `R,RF` |
| `EVENT_COLOR_ID` | variable | Couleur Google Agenda (1 à 11) |

## Développement

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

`CONNECTOR=fichier PLANNING_FILE=exemples/planning.json DRY_RUN=1` fait tourner un passage sans la plateforme.
