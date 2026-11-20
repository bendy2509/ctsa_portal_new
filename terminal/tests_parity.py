"""Tests de parité fonctionnelle, matrice paramétrée rôles × URLs et anti-N+1."""

import datetime

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone

from accounts.models import User
from accounts.permissions import Actions, ROLE_ALLOWLIST
from terminal.models import Container, ShippingLine

# Inventaire de parité officiel des routes sous admin/ et auth/admin-users/.
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
    # Paramètres du site, sous-pages, carrousel & motifs
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


class PortalRoleMatrixAndQueryPerformanceTests(TestCase):
    """Visite toutes les URLs GET d'exploitation avec chaque rôle et vérifie l'absence de N+1."""

    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.yesterday = cls.today - datetime.timedelta(days=1)
        cls.line = ShippingLine.objects.create(name='CMA CGM', code='CMA')
        cls.users = {
            'admin': User.objects.create_user(
                username='mat_admin', password='Password123!', role=User.Role.ADMIN, is_superuser=True, is_staff=True
            ),
            'manager': User.objects.create_user(
                username='mat_manager', password='Password123!', role=User.Role.MANAGER
            ),
            'supervisor': User.objects.create_user(
                username='mat_sup', password='Password123!', role=User.Role.SUPERVISOR
            ),
            'agent': User.objects.create_user(
                username='mat_agent', password='Password123!', role=User.Role.AGENT
            ),
            'client': User.objects.create_user(
                username='mat_client', password='Password123!', role=User.Role.CLIENT
            ),
        }
        cls.container = Container.objects.create(
            container_number='CMAU1000001',
            scheduled_date=cls.today,
            status=Container.Status.CONFIRMED,
            shipping_line=cls.line,
        )
        # Jeu de 12 conteneurs supplémentaires pour tester l'absence de N+1
        for i in range(2, 14):
            Container.objects.create(
                container_number=f'CMAU10000{i:02d}',
                scheduled_date=cls.today if i % 2 == 0 else cls.yesterday,
                status=Container.Status.CONFIRMED,
                shipping_line=cls.line,
            )

    def test_parametrized_all_portal_get_urls_across_every_role(self):
        """Chaque URL GET d'exploitation répond 200 si autorisée, 403 si refusée, 302 si anonyme."""
        routes = [
            ('terminal:dashboard', {}, Actions.PORTAL_DASHBOARD),
            ('terminal:containers', {}, Actions.CONTAINER_LIST),
            ('terminal:container_detail', {'pk': self.container.pk}, Actions.CONTAINER_DETAIL),
            ('terminal:container_edit', {'pk': self.container.pk}, Actions.CONTAINER_CHANGE_STATUS),
            ('terminal:arrival_create', {}, Actions.CONTAINER_ARRIVAL_CREATE),
            ('terminal:prepare', {}, Actions.CONTAINER_PREPARE),
            ('terminal:inspection', {}, Actions.CONTAINER_VERIFY),
            ('terminal:carryover', {}, Actions.CONTAINER_CARRYOVER),
            ('terminal:statistics', {}, Actions.REPORT_VIEW),
            ('terminal:register', {}, Actions.REPORT_VIEW),
            ('terminal:status_logs', {}, Actions.REPORT_VIEW),
            ('terminal:shipping_lines', {}, Actions.SHIPPING_LINE_MANAGE),
            ('terminal:shipping_line_create', {}, Actions.SHIPPING_LINE_MANAGE),
            ('terminal:shipping_line_edit', {'pk': self.line.pk}, Actions.SHIPPING_LINE_MANAGE),
            ('terminal:site_settings', {}, Actions.SITE_SETTINGS_MANAGE),
            ('terminal:site_settings_texts', {}, Actions.SITE_SETTINGS_MANAGE),
            ('terminal:site_settings_windows', {}, Actions.SITE_SETTINGS_MANAGE),
            ('terminal:site_settings_carousel', {}, Actions.SITE_SETTINGS_MANAGE),
            ('terminal:site_settings_motifs', {}, Actions.MOTIF_MANAGE),
            ('accounts:user_list', {}, Actions.USER_MANAGE),
            ('accounts:user_create', {}, Actions.USER_MANAGE),
            ('accounts:user_edit', {'pk': self.users['agent'].pk}, Actions.USER_MANAGE),
        ]

        # Anonyme -> 302 vers connexion
        self.client.logout()
        for url_name, kwargs, _action in routes:
            url = reverse(url_name, kwargs=kwargs)
            with self.subTest(role='anonymous', url=url_name):
                self.assertEqual(self.client.get(url).status_code, 302)

        # Chaque rôle connecté -> 200 ou 403 selon ROLE_ALLOWLIST
        for role_name, user in self.users.items():
            self.client.force_login(user)
            allowlist = ROLE_ALLOWLIST[user.role]
            for url_name, kwargs, action in routes:
                url = reverse(url_name, kwargs=kwargs)
                expected = 200 if (user.is_superuser or action in allowlist) else 403
                with self.subTest(role=role_name, url=url_name):
                    resp = self.client.get(url)
                    self.assertEqual(
                        resp.status_code,
                        expected,
                        f"{role_name} sur {url_name} : attendu {expected}, reçu {resp.status_code}",
                    )

    def test_main_list_views_query_counts_are_bounded(self):
        """Vérifie que les listes principales restent bornées (pas de dérive N+1)."""
        self.client.force_login(self.users['admin'])
        budgets = [
            ('terminal:containers', 20),
            ('terminal:inspection', 25),
            ('terminal:carryover', 25),
            ('terminal:register', 20),
            ('terminal:status_logs', 20),
            ('terminal:shipping_lines', 15),
            ('accounts:user_list', 15),
        ]
        for url_name, max_queries in budgets:
            url = reverse(url_name)
            with self.subTest(url=url_name):
                with CaptureQueriesContext(connection) as ctx:
                    resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200)
                self.assertLessEqual(
                    len(ctx),
                    max_queries,
                    f"{url_name} a exécuté {len(ctx)} requêtes SQL (plafond {max_queries}).",
                )
