# CTSA Portal — Cap Terminal S.A. (Portail RAMPE)

Application web Django pour **Cap Terminal S.A.** (Port du Cap-Haïtien, Haïti) permettant aux clients et commissionnaires en douane de vérifier en temps réel si leur conteneur est prêt pour la vérification douanière à la **RAMPE**, et aux équipes d'exploitation de piloter l'ensemble du flux opérationnel.

## Charte et système de design (`design-system/portail-rampe-ctsa/MASTER.md`)

- **Primaire** : `#1A6394` (survol `#144D73`, contraste 6.45:1 sur `#FFFFFF`)
- **Vert marque CTSA** : `#008751` (réservé au logo et aux filets d'accentuation — jamais aux boutons ni aux statuts)
- **Statuts opérationnels (WCAG AA)** :
  - Confirmé / Vérifié : `#065F46` sur `#ECFDF5` (7.29:1)
  - En attente / Non vérifié : `#92400E` sur `#FEF3C7` (6.37:1)
  - Indisponible : `#991B1B` sur `#FEF2F2` (7.60:1)
  - Enregistré : `#1E293B` sur `#F1F5F9`
- **Polices auto-hébergées** : `Fira Sans` et `Fira Code` (`static/fonts/*.woff2`, `font-display: swap`, ligatures désactivées sur les codes).

## Structure du projet

- [`config/`](config/) — Configuration Django 6.1 (`settings.py`, `urls.py`)
- [`accounts/`](accounts/) — Utilisateurs (5 rôles : `admin`, `manager`, `supervisor`, `agent`, `client`), permissions centralisées (`permissions.py` / `peut()`), vues découpées dans `accounts/views/`
- [`terminal/`](terminal/) — Modèles métier (`models.py`), services (`terminal/services/`), vues découpées par domaine (`terminal/views/`)
- [`templates/`](templates/) — Composants (`components/`), espace d'exploitation par domaine (`portal/`), portail public (`public/`)
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — Architecture détaillée des vues, services, templates et composants
- [`docs/PERMISSIONS.md`](docs/PERMISSIONS.md) — Matrice complète des permissions (`ROLE_ALLOWLIST` et `peut()`)

## Lancer le projet en local

```powershell
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe manage.py runserver
```

## Styles (Tailwind v4 compilé localement)

```powershell
pnpm install
pnpm dev      # développement (watch)
pnpm build    # build minifié (static/css/output.css)
```

## Tests et vérifications

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test accounts terminal
```

## Mise en production

Les réglages sensibles sont lus depuis l'environnement (voir [`.env.example`](.env.example)).
1. Définir `DJANGO_ENV=production`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` et `DJANGO_CSRF_TRUSTED_ORIGINS`.
2. Appliquer les migrations : `python manage.py migrate`.
3. Vérifier : `python manage.py check --deploy`.
4. Rassembler les statiques : `python manage.py collectstatic --noinput` (servi par WhiteNoise).
