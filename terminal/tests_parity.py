"""Tests de parité fonctionnelle : toutes les routes d'exploitation doivent figurer dans l'inventaire."""

from django.test import SimpleTestCase
from django.urls import URLPattern, URLResolver, get_resolver

from accounts.models import User
from accounts.permissions import Actions, ROLE_ALLOWLIST

# Inventaire de parité officiel des routes sous admin/ et auth/admin-users/.
# Chaque route existante de l'espace d'exploitation doit y être déclarée ;
# aucune route ne peut disparaître silencieusement lors de la refonte.
PARITY_INVENTORY = frozenset({
    # Tableau de bord
    'terminal:dashboard',
    # Conteneurs & arrivées
    'terminal:containers',
    'terminal:container_detail',
    'terminal:container_edit',
    'terminal:arrival_create',
    'terminal:container_add_single',
    'terminal:container_add_bulk',
    'terminal:container_update_status',
    'terminal:container_status_bulk',
    'terminal:container_delete',
    'terminal:container_restore',
    'terminal:container_export_pdf',
    'terminal:container_export_csv',
    # Préparer une liste
    'terminal:prepare',
    'terminal:registered_search_json',
    'terminal:registered_add_to_list',
    # Inspection & non vérifiés
    'terminal:inspection',
    'terminal:container_set_verification',
    'terminal:container_verify_bulk',
    'terminal:carryover',
    'terminal:carryover_decide',
    # Rapports, registre & historique
    'terminal:statistics',
    'terminal:register',
    'terminal:status_logs',
    # Lignes maritimes
    'terminal:shipping_lines',
    'terminal:shipping_line_create',
    'terminal:shipping_line_edit',
    'terminal:shipping_line_delete',
    # Paramètres du site, carrousel & motifs
    'terminal:site_settings',
    'terminal:site_settings_texts',
    'terminal:site_settings_windows',
    'terminal:site_settings_carousel',
    'terminal:site_settings_motifs',
    'terminal:hero_slide_create',
    'terminal:hero_slide_toggle',
    'terminal:hero_slide_delete',
    'terminal:motif_create',
    'terminal:motif_toggle',
    'terminal:motif_update',
    # Utilisateurs (espace d'exploitation)
    'accounts:user_list',
    'accounts:user_create',
    'accounts:user_edit',
    'accounts:user_delete',
})


def _collect_portal_routes(patterns, prefix='', namespace=''):
    """Parcourt récursivement urls.py et retourne les noms de routes sous admin/ et auth/admin-users/."""
    found = set()
    for entry in patterns:
        if isinstance(entry, URLResolver):
            sub_prefix = prefix + str(entry.pattern)
            sub_ns = f'{namespace}:{entry.namespace}' if namespace and entry.namespace else (entry.namespace or namespace)
            found.update(_collect_portal_routes(entry.url_patterns, sub_prefix, sub_ns))
        elif isinstance(entry, URLPattern) and entry.name:
            full_path = (prefix + str(entry.pattern)).lstrip('^')
            full_name = f'{namespace}:{entry.name}' if namespace else entry.name
            if (
                full_path.startswith('admin/')
                or '/admin/' in full_path
                or full_path.startswith('auth/admin-users/')
                or '/auth/admin-users/' in full_path
            ):
                # Exclut l'admin technique Django (django-admin/)
                if not full_path.startswith('django-admin/'):
                    found.add(full_name)
    return found


class ParityInventoryTests(SimpleTestCase):
    """Garantit l'exhaustivité de l'inventaire de parité face à urls.py."""

    def test_all_admin_and_user_management_routes_are_in_parity_inventory(self):
        discovered = _collect_portal_routes(get_resolver().url_patterns)
        missing_from_inventory = sorted(discovered - PARITY_INVENTORY)
        missing_from_urls = sorted(PARITY_INVENTORY - discovered)
        self.assertEqual(
            missing_from_inventory,
            [],
            f"Routes présentes dans urls.py mais absentes de PARITY_INVENTORY : {missing_from_inventory}",
        )
        self.assertEqual(
            missing_from_urls,
            [],
            f"Routes présentes dans PARITY_INVENTORY mais supprimées de urls.py : {missing_from_urls}",
        )

    def test_container_restore_permission_matches_role_allowlist(self):
        """Vérifie le comportement réel documenté : seul ADMIN (et superadmin) peut restaurer."""
        roles_with_restore = {
            role for role, perms in ROLE_ALLOWLIST.items() if Actions.CONTAINER_RESTORE in perms
        }
        self.assertEqual(roles_with_restore, {User.Role.ADMIN})
