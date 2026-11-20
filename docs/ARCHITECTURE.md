# Architecture de l'espace d'exploitation — Portail RAMPE (CTSA)

## 1. Organisation des vues et services

### Application `terminal`
- **`terminal/services/`** — Logique de requêtes et agrégats réutilisables (appelle les méthodes métier de `terminal/models.py` sans les réécrire) :
  - `containers.py` : `PAGE_SIZE`, parsing de dates, pagination, calcul du backlog (`unverified_backlog`, `_backlog_breakdown`), reprise (`_carry_into`), clôture (`_close_tracking`), résolution des motifs (`_resolve_motif`).
  - `reports.py` : bornes de période (`_period_bounds`), taux (`_rate`), annotations agrégées (`_stat_annotations`), décompositions par motif (`_reason_breakdown`).
- **`terminal/views/`** — Découpage strict par domaine (`__init__.py` ré-exporte toutes les vues pour compatibilité `urls.py` et tests) :
  - `dashboard.py` : tableau de bord (`terminal:dashboard`).
  - `containers.py` : liste, détail, édition, transitions de statut (unitaire/lot), archivage et restauration.
  - `arrivals.py` : enregistrement d'une arrivée sans liste (ISO 6346).
  - `prepare.py` : préparation de liste, ajout unitaire/lot, recherche JSON et ajout des conteneurs `Enregistré`.
  - `inspection.py` : inspection de séance, marquage Vérifié/Non vérifié, correction et suppression de mouvement.
  - `carryover.py` : suivi des non vérifiés et décisions (`carry` / `reject`).
  - `reports.py` : statistiques, registre des vérifications, historique des statuts, exports CSV et PDF.
  - `shipping_lines.py` : CRUD des lignes maritimes.
  - `settings.py` : page d'accueil Paramètres (`admin/parametres-site/`) + 4 sous-pages (`textes/`, `fenetres/`, `carrousel/`, `motifs/`) + endpoints carrousel et motifs.
  - `public.py` : vues publiques et PWA (hors périmètre exploitation).

### Application `accounts`
- **`accounts/views/`** :
  - `auth.py` : connexion, inscription, déconnexion.
  - `profile.py` : profil personnel (coque portail) / client (coque publique).
  - `users.py` : liste, création, édition, suppression des utilisateurs et journalisation d'audit.

## 2. Templates et composants

- **`templates/portal/base_portal.html`** : coque autonome de l'espace d'exploitation (zéro Google Fonts, lien d'évitement, barre latérale `>= 1024 px` pilotée par `{% can %}`, topbar + barre du bas `< 1024 px` masquée sur les formulaires via `portal-has-form-actions`).
- **`templates/portal/403.html`** : page d'accès refusé adaptée à la coque du rôle.
- **`templates/portal/<domaine>/`** :
  - `dashboard/index.html`
  - `conteneurs/{list,detail,edit,arrival_create,prepare,_status_segmented}.html`
  - `inspection/{session,carryover,_inspection_tabs,_verification_actions}.html`
  - `rapports/{statistics,register,status_logs}.html`
  - `parametres/{shipping_lines,shipping_line_form,settings_home,settings_texts,settings_windows,settings_carousel,settings_motifs}.html`
  - `comptes/{users,user_create,user_edit}.html`
- **`templates/components/`** (15 composants réutilisables) :
  - `page_header`, `card`, `badge`, `list_card`, `data_table`, `filter_bar`, `empty_state`, `form_field`, `form_section`, `form_actions`, `confirm_modal`, `bottom_sheet`, `pagination`, `bottom_nav`, `messages`.

## 3. Design system, CSS, JS et PWA

- **Design system** : [design-system/portail-rampe-ctsa/MASTER.md](../design-system/portail-rampe-ctsa/MASTER.md) (`#1A6394` primaire, `#144D73` survol, `#008751` marque CTSA uniquement, statuts WCAG AA).
- **Polices auto-hébergées** : `static/fonts/fira-{sans,code}-{400..700}.woff2` (`font-display: swap`, `font-variant-ligatures: none` sur le monospace).
- **CSS** : jetons `@theme` dans `static/src/input.css` compilés en `static/css/output.css` (Tailwind v4) ; composants et coque dans `static/css/ctsa.css`.
- **JS** : `static/js/ctsa.js` progressif sans framework, découpé par composant (`initVisualViewportSync`, `initFormValidation`, `initTabs`, `initBottomSheets` avec focus trap / `Échap` / retour focus / conservation saisie, `initConfirmModals`, `initNavigation`, `initSelectionAndControls`, `initRegisteredSearch`).
- **Service Worker** : `templates/pwa/sw.js` en cache `ctsa-rampe-v3` (précharge uniquement la police principale `fira-sans-400.woff2`).
