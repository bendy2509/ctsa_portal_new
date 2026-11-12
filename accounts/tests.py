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
