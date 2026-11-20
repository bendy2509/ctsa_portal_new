from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from terminal.models import Container, Motif, SavedContainer


class PublicPortalPrivacyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_user = User.objects.create_user(
            username='pub_client', password='Password123!', role=User.Role.CLIENT
        )

    def test_no_motif_or_note_exposed_in_public_views_and_json(self):
        today = timezone.localdate()
        unavail_motif = Motif.objects.filter(type=Motif.Type.UNAVAILABLE, is_active=True).first()
        nv_motif = Motif.objects.filter(type=Motif.Type.NOT_VERIFIED, is_active=True).first()

        c_unavail = Container.all_objects.create(
            container_number='CMAU8000001',
            scheduled_date=today,
            status=Container.Status.UNAVAILABLE,
            unavailability_reason=unavail_motif,
            unavailability_note='Note interne confidentielle 123',
        )
        c_nv = Container.all_objects.create(
            container_number='CMAU8000002',
            scheduled_date=today,
            status=Container.Status.CONFIRMED,
        )
        c_nv.set_verification(
            Container.Verification.NOT_VERIFIED,
            reason=nv_motif,
            note='Note inspection confidentielle 456',
        )
        c_reg = Container.all_objects.create(
            container_number='CMAU8000003',
            origin=Container.Origin.ARRIVE_SANS_LISTE,
            status=Container.Status.REGISTERED,
            scheduled_date=None,
        )

        # 1. Fiche publique verification + JSON verification_state
        for num in ('CMAU8000001', 'CMAU8000002'):
            resp = self.client.get(reverse('terminal:verification'), {'q': num})
            self.assertEqual(resp.status_code, 200)
            self.assertNotContains(resp, unavail_motif.label)
            self.assertNotContains(resp, nv_motif.label)
            self.assertNotContains(resp, 'Note interne confidentielle 123')
            self.assertNotContains(resp, 'Note inspection confidentielle 456')

            json_resp = self.client.get(reverse('terminal:verification_state'), {'q': num})
            self.assertEqual(json_resp.status_code, 200)
            self.assertEqual(list(json_resp.json().keys()), ['signature'])

        # 2. Conteneur Enregistré traité comme non programmé
        reg_resp = self.client.get(reverse('terminal:verification'), {'q': 'CMAU8000003'})
        self.assertEqual(reg_resp.status_code, 200)
        self.assertIsNone(reg_resp.context['result'])

        # 3. Watchlist client : la référence client est conservée, aucun motif personnel exposé
        SavedContainer.objects.create(
            user=self.client_user,
            container_number='CMAU8000001',
            label='Ma ref import 99',
        )
        SavedContainer.objects.create(
            user=self.client_user,
            container_number='CMAU8000003',
            label='Mon arrivee 88',
        )
        self.client.force_login(self.client_user)
        wl_resp = self.client.get(reverse('terminal:client_watchlist'))
        self.assertEqual(wl_resp.status_code, 200)
        self.assertContains(wl_resp, 'Ma ref import 99')
        self.assertNotContains(wl_resp, unavail_motif.label)
        self.assertNotContains(wl_resp, 'Note interne confidentielle 123')

        wl_json = self.client.get(reverse('terminal:client_watchlist_state'))
        self.assertEqual(wl_json.status_code, 200)
        self.assertEqual(list(wl_json.json().keys()), ['signature'])

        # 4. Cache service worker incrémenté
        sw_resp = self.client.get(reverse('terminal:pwa_sw'))
        self.assertEqual(sw_resp.status_code, 200)
        self.assertContains(sw_resp, 'ctsa-rampe-v3')
