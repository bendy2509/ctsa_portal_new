from django.test import TestCase
from django.urls import reverse

from accounts.models import User


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
