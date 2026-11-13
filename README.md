# CTSA Portal — Cap Terminal S.A. (Version Django)

Application web pour **Cap Terminal S.A.** (Port du Cap-Haïtien, Haïti) permettant aux clients et commissionnaires en douane de vérifier en temps réel si leur conteneur est prêt pour la vérification douanière du jour à la **RAMPE**.

## Palette de couleurs officielle (`capterminal.com`)

Extraite directement de `https://www.capterminal.com/css/custom.css` et `theme.css` :
- **Slate Maritime Foncé** (Header, Footer, Titres) : `#2c3741` / `#2c3e50` (`#687077` sous-navigation)
- **Bleu Action CTSA** (Boutons, Liens actifs) : `#3498db`
- **Vert Émeraude Logo CTSA** (Statut Confirmé, Identité) : `#009e49` / `#00a651`
- **Rouge Corail CTSA** (Statut Indisponible, Alertes) : `#e74c3c`
- **Polices** : `Raleway` (Titres & Boutons) et `Open Sans` (Corps de texte)

## Structure du projet (générée avec `django-admin startproject` & `startapp`)

- [`manage.py`](manage.py) — Point d'entrée CLI Django
- [`config/settings.py`](config/settings.py) — Configuration Django (SQLite par défaut, prêt pour MySQL/PostgreSQL via `DB_ENGINE`)
- [`accounts/models.py`](accounts/models.py) — Modèle `User` personnalisé avec les 4 rôles (`admin`, `evening_supervisor`, `morning_supervisor`, `client`)
- [`terminal/models.py`](terminal/models.py) — Tables métier :
  1. `shipping_lines` (`ShippingLine`)
  2. `container_prefixes` (`ContainerPrefix`)
  3. `containers` (`Container`) — Unicité sur `(container_number, scheduled_date)`
  4. `container_status_logs` (`ContainerStatusLog`) — Historique automatique à chaque création/modification
  5. `unavailability_reasons` (`UnavailabilityReason`) — Motifs standardisés
  6. `client_saved_containers` (`SavedContainer`) — Tableau de suivi multi-conteneurs pour les clients

## Lancer le projet (dans l'environnement virtuel `.venv`)

```powershell
# Activer l'environnement virtuel
.\.venv\Scripts\Activate.ps1

# Lancer le serveur de développement
.\.venv\Scripts\python.exe manage.py runserver
```

## Comptes de démonstration

Les comptes ci-dessous sont créés par `seed_ctsa`, en développement uniquement. Leur mot de passe est celui que vous donnez dans `CTSA_SEED_PASSWORD` (8 caractères minimum) :

```powershell
$env:CTSA_SEED_PASSWORD = "<votre mot de passe de démonstration>"
.\.venv\Scripts\python.exe manage.py seed_ctsa
```

| Rôle | Identifiant | Accès |
|---|---|---|
| **Administrateur** | `admin` | Accès complet (`/admin/`, préfixes, lignes maritimes, utilisateurs) |
| **Superviseur soir** | `superviseur.soir` | Préparation de la liste du lendemain, ajout unitaire/massif, modification de statut |
| **Superviseur matin** | `superviseur.matin` | Ajout de conteneurs le matin uniquement en statut « En attente » |
| **Client** | `client.import` | Recherche `/verification/` et suivi favoris `/mes-conteneurs/` |

## Administration (`/admin/`)

| Écran | Rôle |
|---|---|
| **Conteneurs du jour** | La liste d'une séance : confirmer, mettre en attente, déclarer indisponible, modifier ou supprimer une ligne |
| **Préparer une liste** | Inscrire un conteneur ou coller une liste, reprendre en un clic les non vérifiés |
| **Inspection** | Marquer Vérifié / Non vérifié les conteneurs confirmés d'une séance |
| **Non vérifiés** | Tout conteneur programmé et pas encore vérifié ; ajout à la liste suivante en un clic, ou rejet |
| **Historique** | Journal de toutes les opérations |
| **Statistiques** | Indicateurs par semaine, mois ou plage libre, export CSV |
| **Registre des vérifications** | Recherche d'un conteneur (à quelle date il a été vérifié), quantités sur un intervalle, export CSV |
| **Paramètres** | Lignes maritimes, motifs d'indisponibilité, utilisateurs (administrateur) |

Après la mise en place du suivi sur une base existante, sortir les anciennes séances du suivi des non vérifiés :

```powershell
.\.venv\Scripts\python.exe manage.py clore_non_verifies --avant 2026-11-01 --simulation
.\.venv\Scripts\python.exe manage.py clore_non_verifies --avant 2026-11-01
```

## Tests

```powershell
.\.venv\Scripts\python.exe manage.py test
```

## Mise en production

Les réglages sensibles sont lus dans l'environnement ; la liste complète est dans [`.env.example`](.env.example).

1. Définir `DJANGO_ENV=production`, `DJANGO_SECRET_KEY` (une clé neuve : celle du dépôt est publique), `DJANGO_ALLOWED_HOSTS` et `DJANGO_CSRF_TRUSTED_ORIGINS`. Sans clé ni hôtes, le démarrage est refusé avec un message explicite.
2. Appliquer les migrations : `python manage.py migrate`.
3. Vérifier la configuration : `python manage.py check --deploy`.
4. Créer le premier administrateur : `python manage.py createsuperuser` (un superutilisateur a tous les droits d'administrateur du portail). `seed_ctsa` est refusée en production.
5. Sauvegarder la base : avec SQLite, copier `db.sqlite3` serveur arrêté ; sinon `python manage.py dumpdata --natural-foreign --indent 2 > sauvegarde.json`.

6. Rassembler les fichiers statiques : `python manage.py collectstatic --noinput`. Ils sont ensuite servis par WhiteNoise, compressés et mis en cache, sans serveur de fichiers séparé.

## Styles (Tailwind compilé en local)

Le portail ne dépend pas du CDN Tailwind : les classes utilisées dans `templates/` et `static/js/` sont compilées par Tailwind v4 dans [`static/css/output.css`](static/css/output.css), qui est commité. La source est [`static/src/input.css`](static/src/input.css) ; elle porte aussi les couleurs et les polices de la charte.

Installer une fois les outils (Node et pnpm requis) :

```powershell
pnpm install
```

Pendant le développement, recompiler à chaque modification :

```powershell
pnpm dev
```

Avant de commiter ou de mettre en ligne, générer le fichier minifié :

```powershell
pnpm build
```

Une classe absente de `output.css` n'a aucun effet : après avoir ajouté ou retiré des classes Tailwind, relancez `pnpm build` et commitez `output.css`. Le serveur de production n'a pas besoin de Node, puisque le fichier compilé est dans le dépôt.

Les styles propres au portail (boutons, badges, tableaux, coque d'administration) sont dans [`static/css/ctsa.css`](static/css/ctsa.css) et ne passent pas par cette compilation.

Les polices (Raleway, Open Sans) restent chargées depuis Google Fonts.
