# Matrice des permissions — Portail RAMPE (Cap Terminal S.A.)

Toute vérification de droit (vues, templates et tags) passe par le point d'entrée unique `peut(utilisateur, action, objet=None)` dans [accounts/permissions.py](../accounts/permissions.py) et le tag de gabarit `{% can 'action' [objet] %}` ([permissions_tags.py](../accounts/templatetags/permissions_tags.py)).

## Principes de sécurité
- **Refus par défaut** : toute action absente de `ROLE_ALLOWLIST` est refusée.
- **Non connecté** → redirection vers `/auth/connexion/?next=...`.
- **Connecté sans droit** → page `403` dédiée ([portal/403.html](../templates/portal/403.html)) dans la coque portail pour le personnel, coque publique pour les clients.
- **Superadmin (`is_superuser=True` ou rôle `admin`)** : accès complet garanti.

## Rôles (5 rôles)
| Rôle (`User.Role`) | Libellé | Périmètre |
|---|---|---|
| `admin` (+ `is_superuser`) | Administrateur | Accès total, paramètres du site, motifs, restauration de conteneurs archivés, gestion des comptes Superadmin |
| `manager` | Manager | Exploitation complète, rapports & exports, lignes maritimes, gestion des comptes hors Superadmin |
| `supervisor` | Superviseur | Tableau de bord, conteneurs, arrivées, préparation de liste, inspection, non vérifiés, archivage dans la fenêtre configurée |
| `agent` | Agent | Tableau de bord, consultation conteneurs/détail, inspection (Vérifié / Non vérifié) et correction de ses propres marquages dans la fenêtre de 60 min |
| `client` | Client | Portail public + liste personnelle « Mes conteneurs » (`client.watchlist`) |

## Matrice `Actions` × Rôles (`ROLE_ALLOWLIST`)

| Action | `admin` / Superadmin | `manager` | `supervisor` | `agent` | `client` |
|---|:---:|:---:|:---:|:---:|:---:|
| `portal.dashboard` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `container.list` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `container.detail` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `container.prepare` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `container.change_status` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `container.arrival_create` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `container.archive` | ✅ | ✅ | ✅ *(fenêtre `delete_window_hours`)* | ❌ | ❌ |
| `container.restore` | ✅ **uniquement** | ❌ | ❌ | ❌ | ❌ |
| `container.verify` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `container.verify_correct` | ✅ | ✅ *(fenêtre `correction_window_minutes`)* | ✅ *(fenêtre)* | ✅ *(auteur + fenêtre)* | ❌ |
| `container.verify_delete_movement` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `container.carryover` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `report.view` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `shipping_line.manage` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `user.manage` | ✅ | ✅ *(hors Superadmin)* | ❌ | ❌ | ❌ |
| `user.manage_superadmin` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `site_settings.manage` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `motif.manage` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `client.watchlist` | ✅ | ✅ | ✅ | ✅ | ✅ |

> **Précision sur `container.restore`** : dans `ROLE_ALLOWLIST`, `Actions.CONTAINER_RESTORE` est accordé exclusivement au rôle `admin` (et aux `is_superuser=True`). Les rôles `manager`, `supervisor`, `agent` et `client` reçoivent `403`.
