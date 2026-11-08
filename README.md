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
| `KIDSYNC_URL`, `KIDSYNC_TOKEN` | Adresse du serveur de kidsync et son jeton : le conteneur `horaires` y va chercher les inscriptions des enfants à chaque passage |
| `KIDSYNC_ENFANTS` | Les enfants suivis (défaut `lyam,noah`), même absents du fichier : leurs anciennes cartes sont retirées de l'agenda |
| `KIDSYNC_FILE` | À la place de l'URL, un fichier local `inscriptions.json` |

## Horaires en dur

Le conteneur `planning-relay-horaires` (même image, `CONNECTOR=horaires`) publie les postes de Rudy, Léa, Luis et Caroline, qui ne sont pas dans Silae, et la poubelle du jeudi (« Poubelle — Jaune » / « Poubelle — Noire », journée entière, catégorie `poubelle`). Ils sont écrits dans [src/planning_relay/donnees/horaires.json](src/planning_relay/donnees/horaires.json), au format repris d'AbView : `fixed` (mêmes horaires chaque semaine), `shift` (rotation sur plusieurs semaines, avec `cycleAnchor` ou `offset`, `weekOverrides` et `exceptions`) ou `allday` (journée entière certains jours, `alternating` par parité de semaine, catégorie donnée par `categorie`).

L'agenda est la copie exacte du fichier : un poste modifié est réécrit sans « (modifié) », un poste retiré est supprimé, et **aucun mail n'est envoyé** (les mails restent réservés au planning Silae). Chaque personne a sa propre clé (`planning_relay=horaires-<prénom>`), le titre est « Prénom — Poste » pour une rotation et le prénom seul pour des horaires fixes, la catégorie `travail` est posée comme pour Silae, et les postes sont en Paon (`EVENT_COLOR_ID=7`, la couleur du travail dans AbView ; la poubelle n'a pas de couleur, champ `couleur` vide). Aucun événement n'a de notification, poubelle comprise : elle est déjà placée la veille du ramassage (le vendredi matin), donc le jeudi. Le champ `rappels` (minutes avant le début) reste disponible si besoin. Le conteneur regarde 2 semaines (`WEEKS=2` : semaine en cours et suivante, soit 7 à 13 jours d'avance, assez pour les 8 jours affichés par AbView) ; les jours passés ne sont jamais touchés. Changer un horaire : modifier le fichier, PR, merge ; Watchtower déploie et le passage suivant met l'agenda à jour.

```sh
docker exec planning-relay-horaires planning-relay sync   # passage immédiat
```

## Développement

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

`CONNECTOR=fichier PLANNING_FILE=exemples/planning.json DRY_RUN=1` fait tourner un passage sans la plateforme.

### Inscriptions des enfants (kidsync)

kidsync (dépôt `Rxdy/kidsync`) relève le planning du portail MJC et le sert en lecture seule (`inscriptions.json` : une entrée par date, enfant et activité, statut `prevu`, `reserve`, `ecart` ou `a_inscrire`). **Le conteneur des horaires** (`CONNECTOR=horaires`) y va chercher les inscriptions dans le même passage que les horaires dès que `KIDSYNC_URL` est défini : pas de conteneur à part, un seul compteur de santé.

- Il recopie **uniquement les inscriptions `reserve`**, comme `horaires` : une clé par groupe d'enfants (`planning_relay=kidsync-lyam-noah`), aucun mail, agenda identique à la source (une inscription qui repasse en écart est retirée).
- **Une carte commune** pour les enfants inscrits pareil le même jour : titre « Lyam et Noah », comme la carte « Garde » ; le libellé « Périscolaire » vient de la catégorie (AbView l'affiche au-dessus). Mercredis MJC de 7h30 à 19h, soirs de 16h30 à 19h. Si un seul enfant est inscrit, ou si leurs horaires diffèrent, chacun a sa carte (« Lyam »). Clés : `kidsync-lyam-noah`, `kidsync-lyam`, `kidsync-noah`. Fenêtre : celle du conteneur (`WEEKS`, deux semaines pour les horaires).
- Catégorie `periscolaire` (mercredis MJC compris), sans couleur Google ni notification. AbView doit la connaître pour la colorer.
- **Cantine** (portail école, « Pause méridienne », 11h30–13h30) : une carte à part « Lyam et Noah » avec sa propre catégorie `cantine` et ses clés (`kidsync-cantine-lyam-noah`…), séparée du périscolaire d'un même jour. Seules les cantines réservées sont publiées.
- Variables : `KIDSYNC_URL` (adresse du serveur de kidsync) et `KIDSYNC_TOKEN` (son jeton), à mettre dans le `.env` du Pi. `KIDSYNC_FILE` lit un fichier local à la place.
- Si le serveur de kidsync est injoignable (le poste est éteint), le passage ne change rien aux inscriptions et n'est pas compté comme un échec : les horaires sont publiés comme d'habitude. Un jeton refusé, lui, est un échec.
- `CONNECTOR=kidsync` existe aussi pour un conteneur qui ne ferait que les inscriptions ; ce n'est pas nécessaire.
- Essai sans rien écrire : `CONNECTOR=horaires KIDSYNC_FILE=…/inscriptions.json DRY_RUN=1`.
