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

## Comptes de démonstration (Mot de passe : `Ctsa2026!`)

| Rôle | Identifiant | Accès |
|---|---|---|
| **Administrateur** | `admin` | Accès complet (`/admin/`, préfixes, lignes maritimes, utilisateurs) |
| **Superviseur soir** | `superviseur.soir` | Préparation de la liste du lendemain, ajout unitaire/massif, modification de statut |
| **Superviseur matin** | `superviseur.matin` | Ajout de conteneurs le matin uniquement en statut « En attente » |
| **Client** | `client.import` | Recherche `/verification/` et suivi favoris `/mes-conteneurs/` |
