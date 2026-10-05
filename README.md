# planning-relay

Recopie toutes les heures un planning de travail (Silae RH Suite) dans le Google Agenda lu par l'affichage familial. Envoie un mail récapitulatif à chaque changement.

Un passage : lire la ligne de la personne suivie (les collègues sont écartés dès la lecture), comparer avec l'agenda, qui sert d'état de référence, envoyer un mail s'il y a des écarts, puis écrire dans l'agenda. Seuls les événements portant la propriété privée `planning_relay` sont touchés.

## Branches

`dev` → `staging` → `main`, toutes protégées : passage par une pull request et CI verte obligatoires. Chaque merge publie l'image `ghcr.io/rxdy/planning-relay` avec le tag `dev`, `staging` ou `latest`.

## Exécution sur rp-meliodas

Comme les autres services du Pi : un dossier `~/planning-relay` avec `docker-compose.prod.yml` et un `.env` en `600`. Le conteneur `planning-relay` tourne en permanence et fait un passage à chaque heure pile (hh:00, heure de Paris), jamais au démarrage. Watchtower le met à jour à chaque nouvelle image `latest`, et metryx l'affiche avec son état de santé. Les fichiers sont dans [deploy/](deploy/).

```sh
# sur le Pi
mkdir -p ~/planning-relay && cd ~/planning-relay
# copier deploy/docker-compose.prod.yml et deploy/.env.example (→ .env, à remplir)
chmod 600 .env
docker compose -f docker-compose.prod.yml up -d
docker logs -f planning-relay          # journal des passages
docker exec planning-relay planning-relay sync   # passage immédiat, si besoin
```

Le conteneur passe en `unhealthy` après 3 échecs d'affilée, ou si aucun passage n'a eu lieu depuis 2 h 30. Au 3e échec, un mail d'alerte part, une seule fois par série. L'état vit dans le volume `planning-relay-data`.

| Variable | Rôle |
|---|---|
| `PLATFORM_USER`, `PLATFORM_PASSWORD` | Connexion à sirh.software |
| `PERSON_MATCH` | Matricule Silae de la personne suivie (champ `employee`) |
| `GOOGLE_SA_JSON` | Clé du compte de service (scope `calendar.events`) |
| `CALENDAR_ID` | Agenda de l'affichage |
| `SMTP_USER`, `SMTP_PASSWORD` | Expéditeur Gmail et mot de passe d'application |
| `MAIL_TO` | Destinataires, séparés par des virgules |
| `SKIP_CODES` | Codes à ne pas reporter (défaut `R,RF` : un jour vide est un repos) |
| `EVENT_COLOR_ID` | Couleur Google Agenda (1 à 11) |
| `DRY_RUN` | `1` : ne rien écrire ni envoyer |

## Développement

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

`CONNECTOR=fichier PLANNING_FILE=exemples/planning.json DRY_RUN=1` fait tourner un passage sans la plateforme.
