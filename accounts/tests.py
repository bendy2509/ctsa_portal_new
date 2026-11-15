from django.test import TestCase
from django.urls import reverse

from accounts.models import User


class ProductionSettingsTests(TestCase):
    """En production, le portail refuse de démarrer sans clé secrète ni hôtes déclarés."""

    def run_check(self, **env_overrides):
        import os
        import subprocess
        import sys

        env = {k: v for k, v in os.environ.items() if not k.startswith('DJANGO_')}
        env.update(env_overrides)
        return subprocess.run(
            [sys.executable, 'manage.py', 'check'], capture_output=True, text=True, env=env, timeout=120
        )

    def test_production_requires_secret_key_and_hosts(self):
        missing_key = self.run_check(DJANGO_ENV='production')
        self.assertNotEqual(missing_key.returncode, 0)
        self.assertIn('DJANGO_SECRET_KEY est obligatoire', missing_key.stderr)

        missing_hosts = self.run_check(DJANGO_ENV='production', DJANGO_SECRET_KEY='x' * 60)
        self.assertNotEqual(missing_hosts.returncode, 0)
        self.assertIn('DJANGO_ALLOWED_HOSTS est obligatoire', missing_hosts.stderr)

        complete = self.run_check(
            DJANGO_ENV='production', DJANGO_SECRET_KEY='x' * 60, DJANGO_ALLOWED_HOSTS='rampe.example.test'
        )
        self.assertEqual(complete.returncode, 0, complete.stderr)

    def test_development_starts_without_any_variable(self):
        self.assertEqual(self.run_check().returncode, 0)


class LocalAssetsTests(TestCase):
    """Les pages n'appellent plus le CDN Tailwind ni le logo distant, et le CSS compilé est à jour."""

    def test_pages_use_project_assets_only(self):
        html = self.client.get(reverse('accounts:login')).content.decode()
        self.assertNotIn('cdn.tailwindcss.com', html)
        self.assertNotIn('capterminal.com/img', html)
        self.assertIn('css/output.css', html)
        self.assertIn('img/cap-terminal-logo.png', html)

    def test_compiled_tailwind_covers_the_classes_used_by_templates(self):
        import re
        from pathlib import Path
        from django.conf import settings

        css = (settings.BASE_DIR / 'static' / 'css' / 'output.css').read_text(encoding='utf-8')
        # Classes à valeur arbitraire (ex. bg-[#fff6e0]) : les plus faciles à oublier de recompiler.
        used = set()
        for template in (settings.BASE_DIR / 'templates').rglob('*.html'):
            used.update(re.findall(r'(?<![\w:-])(?:bg|text|border)-\[#[0-9a-fA-F]{6}\]', template.read_text(encoding='utf-8')))
        missing = sorted(
            cls for cls in used
            if '.' + cls.replace('[', '\\[').replace(']', '\\]').replace('#', '\\#') not in css
        )
        self.assertEqual(missing, [], "Relancez la compilation Tailwind : pnpm build.")


class LoginRedirectTests(TestCase):
    """Après connexion, on ne suit `next` que s'il reste sur le portail."""

    def setUp(self):
        User.objects.create_user(username='cli', password='Password123!', role=User.Role.CLIENT)
        self.credentials = {'username': 'cli', 'password': 'Password123!'}

    def test_internal_next_brings_the_client_back_to_the_container(self):
        target = '/verification/?q=CMAU4052450'
        resp = self.client.post(reverse('accounts:login') + '?next=%2Fverification%2F%3Fq%3DCMAU4052450', self.credentials)
        self.assertRedirects(resp, target, fetch_redirect_response=False)

    def test_external_next_is_ignored(self):
        for hostile in ('https://exemple-externe.test/piege', '//exemple-externe.test/piege'):
            self.client.logout()
            resp = self.client.post(reverse('accounts:login'), self.credentials, QUERY_STRING=f'next={hostile}')
            self.assertRedirects(resp, reverse('terminal:client_watchlist'), fetch_redirect_response=False)


class InactivityTimeoutTests(TestCase):
    """La session expire après 5 minutes d'inactivité réelle, hors rafraîchissements automatiques."""

    def setUp(self):
        User.objects.create_user(username='sup', password='Password123!', role=User.Role.SUPERVISOR)
        self.client.login(username='sup', password='Password123!')

    def test_active_user_stays_logged_in_and_background_poll_does_not_extend(self):
        import time

        resp = self.client.get(reverse('terminal:dashboard'))
        self.assertEqual(resp.status_code, 200)
        recorded = self.client.session['_last_activity']

        # Simule un rafraîchissement automatique en arrière-plan 4 min plus tard :
        # _last_activity ne doit pas être mis à jour.
        old_ts = time.time() - 240
        session = self.client.session
        session['_last_activity'] = old_ts
        session.save()

        self.client.get(reverse('terminal:verification_state'), {'q': 'CMAU4052450'})
        self.assertEqual(self.client.session['_last_activity'], old_ts)
        self.assertGreater(recorded, old_ts)

    def test_user_is_logged_out_after_5_minutes_of_inactivity(self):
        import time

        self.assertEqual(self.client.get(reverse('terminal:dashboard')).status_code, 200)

        session = self.client.session
        session['_last_activity'] = time.time() - 301
        session.save()

        resp = self.client.get(reverse('terminal:dashboard'))
        self.assertRedirects(resp, f"{reverse('accounts:login')}?next={reverse('terminal:dashboard')}")


class UserManagementPermissionsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin_mgr',
            password='Password123!',
            role=User.Role.ADMIN,
        )
        self.supervisor = User.objects.create_user(
            username='sup_nonadmin',
            password='Password123!',
            role=User.Role.SUPERVISOR,
        )

    def test_non_admin_cannot_access_user_management(self):
        self.client.login(username='sup_nonadmin', password='Password123!')
        for route in [
            reverse('accounts:user_list'),
            reverse('accounts:user_create'),
            reverse('accounts:user_edit', args=[self.admin.pk]),
        ]:
            resp = self.client.get(route)
            self.assertEqual(resp.status_code, 302)

        del_resp = self.client.post(reverse('accounts:user_delete', args=[self.admin.pk]))
        self.assertEqual(del_resp.status_code, 302)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_admin_can_create_edit_and_delete_user(self):
        self.client.login(username='admin_mgr', password='Password123!')
        create_get = self.client.get(reverse('accounts:user_create'))
        self.assertEqual(create_get.status_code, 200)
        self.assertContains(create_get, 'portal-has-form-actions')

        create_post = self.client.post(
            reverse('accounts:user_create'),
            {
                'username': 'new_agent',
                'full_name': 'Agent Test',
                'role': User.Role.SUPERVISOR,
                'password': 'Password123!',
                'is_active': 'on',
            },
        )
        self.assertRedirects(create_post, reverse('accounts:user_list'))
        created = User.objects.get(username='new_agent')

        edit_get = self.client.get(reverse('accounts:user_edit', args=[created.pk]))
        self.assertEqual(edit_get.status_code, 200)
        self.assertContains(edit_get, 'portal-has-form-actions')

        edit_post = self.client.post(
            reverse('accounts:user_edit', args=[created.pk]),
            {
                'username': 'new_agent',
                'full_name': 'Agent Modifié',
                'role': User.Role.SUPERVISOR,
                'is_active': 'on',
            },
        )
        self.assertRedirects(edit_post, reverse('accounts:user_list'))
        created.refresh_from_db()
        self.assertEqual(created.full_name, 'Agent Modifié')

        del_post = self.client.post(reverse('accounts:user_delete', args=[created.pk]))
        self.assertRedirects(del_post, reverse('accounts:user_list'))
        self.assertFalse(User.objects.filter(pk=created.pk).exists())
