from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from terminal.models import Container, is_valid_iso6346, normalize_container_number


class ArrivalCreationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.supervisor = User.objects.create_user(
            username='arr_sup', password='Password123!', role=User.Role.SUPERVISOR
        )
        cls.agent = User.objects.create_user(
            username='arr_agent', password='Password123!', role=User.Role.AGENT
        )

    def test_iso6346_and_normalization(self):
        self.assertEqual(normalize_container_number(' cmau-405.245 0 '), 'CMAU4052450')
        # MSKU9070323 : clé ISO 6346 valide
        self.assertTrue(is_valid_iso6346('MSKU9070323'))
        self.assertFalse(is_valid_iso6346('MSKU9070329'))
        self.assertFalse(is_valid_iso6346('MSKA9070323'))  # 4e lettre != U/J/Z

    def test_permissions_agent_forbidden_supervisor_allowed(self):
        self.client.force_login(self.agent)
        self.assertEqual(self.client.get(reverse('terminal:arrival_create')).status_code, 403)
        self.client.force_login(self.supervisor)
        resp = self.client.get(reverse('terminal:arrival_create'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'portal-has-form-actions')
        self.assertContains(resp, 'autocapitalize="characters"')

    def test_create_arrival_valid_iso_and_duplicate_detection(self):
        self.client.force_login(self.supervisor)
        resp = self.client.post(
            reverse('terminal:arrival_create'),
            {'container_number': ' msku-907.032 3 ', 'shipping_line': ''},
        )
        c = Container.registered_objects.get(normalized_number='MSKU9070323')
        self.assertRedirects(resp, reverse('terminal:container_detail', args=[c.pk]))
        self.assertEqual(c.status, Container.Status.REGISTERED)
        self.assertEqual(c.origin, Container.Origin.ARRIVE_SANS_LISTE)
        self.assertIsNone(c.scheduled_date)
        self.assertIsNotNone(c.arrived_at)
        self.assertNotIn(c, Container.objects.all())

        # Doublon détecté
        dup_resp = self.client.post(
            reverse('terminal:arrival_create'),
            {'container_number': 'MSKU9070323', 'shipping_line': ''},
        )
        self.assertEqual(dup_resp.status_code, 200)
        self.assertContains(dup_resp, 'Doublon détecté')
        self.assertContains(dup_resp, reverse('terminal:container_detail', args=[c.pk]))
        self.assertEqual(Container.all_objects.filter(normalized_number='MSKU9070323').count(), 1)

    def test_invalid_iso6346_shows_non_blocking_warning_and_allows_confirm(self):
        self.client.force_login(self.supervisor)
        warn_resp = self.client.post(
            reverse('terminal:arrival_create'),
            {'container_number': 'MSKU9070329', 'shipping_line': ''},
        )
        self.assertEqual(warn_resp.status_code, 200)
        self.assertContains(warn_resp, 'Clé de contrôle ISO 6346 invalide')
        self.assertFalse(Container.all_objects.filter(normalized_number='MSKU9070329').exists())

        ok_resp = self.client.post(
            reverse('terminal:arrival_create'),
            {'container_number': 'MSKU9070329', 'shipping_line': '', 'confirm_iso': '1'},
        )
        c = Container.registered_objects.get(normalized_number='MSKU9070329')
        self.assertRedirects(ok_resp, reverse('terminal:container_detail', args=[c.pk]))
