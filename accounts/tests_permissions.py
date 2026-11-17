import datetime
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import get_resolver, reverse
from django.utils import timezone

from accounts.models import User
from accounts.permissions import Actions, ROLE_ALLOWLIST, URL_PERMISSIONS, peut
from terminal.models import (
    Container,
    Motif,
    PortalAuditLog,
    SiteSettings,
    VerificationEvent,
)


def _collect_named_urls():
    """Parcourt toutes les URLs nommées de `accounts` et `terminal`."""
    resolver = get_resolver()
    found = []

    def _walk(patterns, namespace=''):
        for p in patterns:
            if hasattr(p, 'url_patterns'):
                ns = p.namespace or namespace
                _walk(p.url_patterns, ns)
            elif p.name and namespace in ('accounts', 'terminal'):
                full_name = f"{namespace}:{p.name}"
                # Construire une URL de test avec pk=1 si le pattern attend un <int:pk>
                route = str(p.pattern)
                kwargs = {'pk': 1} if '<int:pk>' in route or '<pk>' in route else {}
                found.append((full_name, kwargs))

    _walk(resolver.url_patterns)
    return found


class PermissionsMatrixAndDefaultDenyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superadmin = User.objects.create_user(
            username='sa_test',
            password='Password123!',
            role=User.Role.ADMIN,
            is_superuser=True,
            is_staff=True,
        )
        cls.admin = User.objects.create_user(
            username='adm_test',
            password='Password123!',
            role=User.Role.ADMIN,
        )
        cls.supervisor = User.objects.create_user(
            username='sup_test',
            password='Password123!',
            role=User.Role.SUPERVISOR,
        )
        cls.agent = User.objects.create_user(
            username='agent_test',
            password='Password123!',
            role=User.Role.AGENT,
        )
        cls.other_agent = User.objects.create_user(
            username='agent_other',
            password='Password123!',
            role=User.Role.AGENT,
        )
        cls.client_user = User.objects.create_user(
            username='client_test',
            password='Password123!',
            role=User.Role.CLIENT,
        )
        cls.container = Container.all_objects.create(
            pk=1,
            container_number='CMAU1234567',
            scheduled_date=timezone.localdate(),
            status=Container.Status.CONFIRMED,
        )

    def test_superadmin_always_allowed(self):
        all_actions = [
            v for k, v in vars(Actions).items()
            if not k.startswith('_') and isinstance(v, str)
        ]
        for action in all_actions:
            with self.subTest(action=action):
                self.assertTrue(peut(self.superadmin, action))
                self.assertTrue(peut(self.superadmin, action, self.container))

    def test_matrix_role_by_action(self):
        expected = {
            Actions.PORTAL_DASHBOARD: {'agent': True, 'supervisor': True, 'admin': True, 'superadmin': True, 'client': False},
            Actions.CONTAINER_LIST: {'agent': True, 'supervisor': True, 'admin': True, 'superadmin': True, 'client': False},
            Actions.CONTAINER_DETAIL: {'agent': True, 'supervisor': True, 'admin': True, 'superadmin': True, 'client': False},
            Actions.CONTAINER_PREPARE: {'agent': False, 'supervisor': True, 'admin': True, 'superadmin': True, 'client': False},
            Actions.CONTAINER_CHANGE_STATUS: {'agent': False, 'supervisor': True, 'admin': True, 'superadmin': True, 'client': False},
            Actions.CONTAINER_ARRIVAL_CREATE: {'agent': False, 'supervisor': True, 'admin': True, 'superadmin': True, 'client': False},
            Actions.CONTAINER_ARCHIVE: {'agent': False, 'supervisor': True, 'admin': True, 'superadmin': True, 'client': False},
            Actions.CONTAINER_RESTORE: {'agent': False, 'supervisor': False, 'admin': False, 'superadmin': True, 'client': False},
            Actions.CONTAINER_VERIFY: {'agent': True, 'supervisor': True, 'admin': True, 'superadmin': True, 'client': False},
            Actions.CONTAINER_VERIFY_CORRECT: {'agent': True, 'supervisor': True, 'admin': True, 'superadmin': True, 'client': False},
            Actions.CONTAINER_VERIFY_DELETE_MOVEMENT: {'agent': False, 'supervisor': True, 'admin': True, 'superadmin': True, 'client': False},
            Actions.CONTAINER_CARRYOVER: {'agent': False, 'supervisor': True, 'admin': True, 'superadmin': True, 'client': False},
            Actions.REPORT_VIEW: {'agent': False, 'supervisor': False, 'admin': True, 'superadmin': True, 'client': False},
            Actions.SHIPPING_LINE_MANAGE: {'agent': False, 'supervisor': False, 'admin': True, 'superadmin': True, 'client': False},
            Actions.USER_MANAGE_AGENT_SUPERVISOR: {'agent': False, 'supervisor': False, 'admin': True, 'superadmin': True, 'client': False},
            Actions.USER_MANAGE_ADMIN: {'agent': False, 'supervisor': False, 'admin': True, 'superadmin': True, 'client': False},
            Actions.USER_MANAGE_SUPERADMIN: {'agent': False, 'supervisor': False, 'admin': False, 'superadmin': True, 'client': False},
            Actions.SITE_SETTINGS_MANAGE: {'agent': False, 'supervisor': False, 'admin': False, 'superadmin': True, 'client': False},
            Actions.MOTIF_MANAGE: {'agent': False, 'supervisor': False, 'admin': False, 'superadmin': True, 'client': False},
            Actions.CLIENT_WATCHLIST: {'agent': True, 'supervisor': True, 'admin': True, 'superadmin': True, 'client': True},
        }
        roles = {
            'agent': self.agent,
            'supervisor': self.supervisor,
            'admin': self.admin,
            'superadmin': self.superadmin,
            'client': self.client_user,
        }
        for action, matrix in expected.items():
            for role_name, user in roles.items():
                with self.subTest(action=action, role=role_name):
                    self.assertEqual(peut(user, action), matrix[role_name])

    def test_unknown_action_denied_for_non_superadmin(self):
        for user in (self.agent, self.supervisor, self.admin, self.client_user):
            self.assertFalse(peut(user, 'future.unknown_action'))
        self.assertTrue(peut(self.superadmin, 'future.unknown_action'))

    def test_all_urls_default_deny_for_all_roles(self):
        """Parcourt TOUTES les URLs de l'app : toute URL hors liste blanche répond 403."""
        named_urls = _collect_named_urls()
        post_only_urls = {
            'accounts:logout',
            'accounts:user_delete',
            'terminal:client_watchlist_remove',
            'terminal:container_add_single',
            'terminal:container_add_bulk',
            'terminal:container_update_status',
            'terminal:container_status_bulk',
            'terminal:container_delete',
            'terminal:container_set_verification',
            'terminal:container_verify_bulk',
            'terminal:carryover_decide',
            'terminal:shipping_line_delete',
            'terminal:hero_slide_create',
            'terminal:hero_slide_toggle',
            'terminal:hero_slide_delete',
        }
        roles = [
            ('agent', self.agent),
            ('supervisor', self.supervisor),
            ('admin', self.admin),
            ('client', self.client_user),
        ]
        for role_name, user in roles:
            self.client.force_login(user)
            allowlist = ROLE_ALLOWLIST[user.role]
            for url_name, kwargs in named_urls:
                if url_name not in URL_PERMISSIONS:
                    continue
                required_action = URL_PERMISSIONS[url_name]
                if required_action in allowlist:
                    continue
                url = reverse(url_name, kwargs=kwargs)
                with self.subTest(role=role_name, url=url_name):
                    if url_name in post_only_urls:
                        resp = self.client.post(url)
                    else:
                        resp = self.client.get(url)
                    self.assertEqual(resp.status_code, 403, f"{role_name} devrait recevoir 403 sur {url_name}")
                    self.assertContains(resp, "Accès refusé", status_code=403)

    def test_anonymous_redirected_to_login_on_protected_urls(self):
        for url_name in ('terminal:dashboard', 'terminal:site_settings', 'accounts:user_list'):
            resp = self.client.get(reverse(url_name))
            self.assertEqual(resp.status_code, 302)
            self.assertIn('/auth/connexion/', resp.url)

    def test_correction_window_boundaries_and_agent_ownership(self):
        SiteSettings.get_solo()
        base_time = timezone.now()
        event = VerificationEvent.objects.create(
            container=self.container,
            result=Container.Verification.VERIFIED,
            created_by=self.agent,
            created_at=base_time,
        )
        # 59 minutes -> autorisé pour l'agent propriétaire et superviseur/admin
        with patch('django.utils.timezone.now', return_value=base_time + datetime.timedelta(minutes=59)):
            self.assertTrue(peut(self.agent, Actions.CONTAINER_VERIFY_CORRECT, event))
            self.assertFalse(peut(self.other_agent, Actions.CONTAINER_VERIFY_CORRECT, event))
            self.assertTrue(peut(self.supervisor, Actions.CONTAINER_VERIFY_CORRECT, event))
            self.assertTrue(peut(self.admin, Actions.CONTAINER_VERIFY_CORRECT, event))

        # 61 minutes -> refusé pour agent, superviseur, admin ; autorisé pour superadmin
        with patch('django.utils.timezone.now', return_value=base_time + datetime.timedelta(minutes=61)):
            self.assertFalse(peut(self.agent, Actions.CONTAINER_VERIFY_CORRECT, event))
            self.assertFalse(peut(self.supervisor, Actions.CONTAINER_VERIFY_CORRECT, event))
            self.assertFalse(peut(self.admin, Actions.CONTAINER_VERIFY_CORRECT, event))
            self.assertTrue(peut(self.superadmin, Actions.CONTAINER_VERIFY_CORRECT, event))

    def test_archive_window_boundaries_arrived_vs_created(self):
        SiteSettings.get_solo()
        base_time = timezone.now()
        arrived_container = Container.all_objects.create(
            container_number='MSCU7654321',
            origin=Container.Origin.ARRIVE_SANS_LISTE,
            status=Container.Status.REGISTERED,
            scheduled_date=None,
            arrived_at=base_time,
        )
        # 23h59 -> Superviseur autorisé
        with patch('django.utils.timezone.now', return_value=base_time + datetime.timedelta(hours=23, minutes=59)):
            self.assertTrue(peut(self.supervisor, Actions.CONTAINER_ARCHIVE, arrived_container))
            self.assertTrue(peut(self.admin, Actions.CONTAINER_ARCHIVE, arrived_container))

        # 24h01 -> Superviseur refusé, Admin et Superadmin autorisés
        with patch('django.utils.timezone.now', return_value=base_time + datetime.timedelta(hours=24, minutes=1)):
            self.assertFalse(peut(self.supervisor, Actions.CONTAINER_ARCHIVE, arrived_container))
            self.assertTrue(peut(self.admin, Actions.CONTAINER_ARCHIVE, arrived_container))
            self.assertTrue(peut(self.superadmin, Actions.CONTAINER_ARCHIVE, arrived_container))

    def test_last_superadmin_protected_and_admin_cannot_touch_superadmin(self):
        # Admin ne peut ni éditer ni supprimer le Superadmin
        self.client.force_login(self.admin)
        resp_edit = self.client.get(reverse('accounts:user_edit', args=[self.superadmin.pk]))
        self.assertEqual(resp_edit.status_code, 403)
        resp_del = self.client.post(reverse('accounts:user_delete', args=[self.superadmin.pk]))
        self.assertEqual(resp_del.status_code, 403)

        # Le dernier Superadmin ne peut pas se désactiver ni être supprimé
        self.client.force_login(self.superadmin)
        self.client.post(
            reverse('accounts:user_edit', args=[self.superadmin.pk]),
            {
                'username': self.superadmin.username,
                'full_name': 'SA',
                'email': 'sa@example.com',
                'role': User.Role.ADMIN,
                'company_name': '',
                'phone': '',
                'is_active': '',
            },
        )
        self.superadmin.refresh_from_db()
        self.assertTrue(self.superadmin.is_active)

    def test_promote_superadmin_command_and_append_only_audit_log(self):
        call_command('promote_superadmin', self.admin.username)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_superuser)
        log = PortalAuditLog.objects.filter(
            category=PortalAuditLog.Category.PROMOTE_SUPERADMIN,
            target_label=f"User:{self.admin.username}",
        ).first()
        self.assertIsNotNone(log)

        # Journal d'audit en ajout seulement
        with self.assertRaises(ValidationError):
            log.reason = "Modifié"
            log.save()
        with self.assertRaises(ValidationError):
            log.delete()

    def test_default_manager_excludes_registered_and_archived_and_verification_sync(self):
        reg = Container.all_objects.create(
            container_number='ANTU1111111',
            origin=Container.Origin.ARRIVE_SANS_LISTE,
            status=Container.Status.REGISTERED,
            scheduled_date=None,
        )
        arch = Container.all_objects.create(
            container_number='ANTU2222222',
            scheduled_date=timezone.localdate(),
            status=Container.Status.PENDING,
            archived_at=timezone.now(),
        )
        self.assertNotIn(reg, Container.objects.all())
        self.assertNotIn(arch, Container.objects.all())
        self.assertIn(reg, Container.registered_objects.all())
        self.assertIn(reg, Container.all_objects.all())
        self.assertIn(arch, Container.all_objects.all())

        # Cohérence VerificationEvent -> Container.verification_result
        self.container.set_verification(Container.Verification.VERIFIED, acting_user=self.agent)
        self.container.refresh_from_db()
        self.assertEqual(self.container.verification_result, Container.Verification.VERIFIED)
        self.assertEqual(self.container.verified_by, self.agent)

        self.container.set_verification('', acting_user=self.supervisor, note='Annulation test')
        self.container.refresh_from_db()
        self.assertEqual(self.container.verification_result, '')
        self.assertIsNone(self.container.verified_by)

        # Motif jamais supprimable
        motif = Motif.objects.first()
        with self.assertRaises(ValidationError):
            motif.delete()

    def test_superadmin_can_create_superadmin_and_admin_cannot(self):
        # 1. Superadmin voit la case is_superuser et peut créer un autre Superadmin
        self.client.force_login(self.superadmin)
        get_sa = self.client.get(reverse('accounts:user_create'))
        self.assertContains(get_sa, 'name="is_superuser"')

        resp_sa = self.client.post(
            reverse('accounts:user_create'),
            {
                'username': 'sa_second',
                'full_name': 'Deuxième Superadmin',
                'role': User.Role.ADMIN,
                'password': 'Password123!',
                'is_active': 'on',
                'is_superuser': 'on',
            },
        )
        self.assertRedirects(resp_sa, reverse('accounts:user_list'))
        created_sa = User.objects.get(username='sa_second')
        self.assertTrue(created_sa.is_superuser)
        self.assertTrue(created_sa.is_staff)

        # 2. Admin ne voit pas is_superuser, reçoit 403 s'il tente de créer ou promouvoir un Superadmin
        self.client.force_login(self.admin)
        get_adm = self.client.get(reverse('accounts:user_create'))
        self.assertNotContains(get_adm, 'name="is_superuser"')
        self.assertNotContains(get_adm, reverse('terminal:site_settings'))

        resp_forbid_create = self.client.post(
            reverse('accounts:user_create'),
            {
                'username': 'fake_sa',
                'full_name': 'Fake SA',
                'role': User.Role.ADMIN,
                'password': 'Password123!',
                'is_active': 'on',
                'is_superuser': 'on',
            },
        )
        self.assertEqual(resp_forbid_create.status_code, 403)
        self.assertFalse(User.objects.filter(username='fake_sa').exists())

        resp_forbid_edit = self.client.post(
            reverse('accounts:user_edit', args=[self.agent.pk]),
            {
                'username': self.agent.username,
                'full_name': 'Agent Promoted',
                'role': User.Role.ADMIN,
                'is_active': 'on',
                'is_superuser': 'on',
            },
        )
        self.assertEqual(resp_forbid_edit.status_code, 403)
        self.agent.refresh_from_db()
        self.assertFalse(self.agent.is_superuser)

        # 3. Admin peut créer tous les autres rôles (Agent, Superviseur, Admin, Client)
        for role_val in (User.Role.AGENT, User.Role.SUPERVISOR, User.Role.ADMIN, User.Role.CLIENT):
            uname = f"adm_created_{role_val}"
            ok_resp = self.client.post(
                reverse('accounts:user_create'),
                {
                    'username': uname,
                    'full_name': f"User {role_val}",
                    'role': role_val,
                    'password': 'Password123!',
                    'is_active': 'on',
                },
            )
            self.assertRedirects(ok_resp, reverse('accounts:user_list'))
            u = User.objects.get(username=uname)
            self.assertEqual(u.role, role_val)
            self.assertFalse(u.is_superuser)
