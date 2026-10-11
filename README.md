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

## Horaires en dur

Le conteneur `planning-relay-horaires` (même image, `CONNECTOR=horaires`) publie les postes de Rudy, Léa, Luis et Caroline, qui ne sont pas dans Silae, et la poubelle du jeudi (« Poubelle — Jaune » / « Poubelle — Noire », journée entière, catégorie `poubelle`). Ils sont écrits dans [src/planning_relay/donnees/horaires.json](src/planning_relay/donnees/horaires.json), au format repris d'AbView : `fixed` (mêmes horaires chaque semaine), `shift` (rotation sur plusieurs semaines, avec `cycleAnchor` ou `offset`, `weekOverrides` et `exceptions`) ou `allday` (journée entière certains jours, `alternating` par parité de semaine, catégorie donnée par `categorie`).

L'agenda est la copie exacte du fichier : un poste modifié est réécrit sans « (modifié) », un poste retiré est supprimé, et **aucun mail n'est envoyé** (les mails restent réservés au planning Silae). Chaque personne a sa propre clé (`planning_relay=horaires-<prénom>`), le titre est « Prénom — Poste » pour une rotation et le prénom seul pour des horaires fixes, la catégorie `travail` est posée comme pour Silae, et les postes sont en Paon (`EVENT_COLOR_ID=7`, la couleur du travail dans AbView ; la poubelle n'a pas de couleur, champ `couleur` vide). Aucun poste n'a de notification (ce sont des événements quotidiens) ; seule la poubelle en a une, la veille à 19h (champ `rappels`, en minutes avant minuit). Le conteneur regarde 2 semaines (`WEEKS=2` : semaine en cours et suivante, soit 7 à 13 jours d'avance, assez pour les 8 jours affichés par AbView) ; les jours passés ne sont jamais touchés. Changer un horaire : modifier le fichier, PR, merge ; Watchtower déploie et le passage suivant met l'agenda à jour.

```sh
docker exec planning-relay-horaires planning-relay sync   # passage immédiat
```

## Développement

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

`CONNECTOR=fichier PLANNING_FILE=exemples/planning.json DRY_RUN=1` fait tourner un passage sans la plateforme.
