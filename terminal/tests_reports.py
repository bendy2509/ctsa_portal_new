import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from accounts.models import User
from terminal.models import Container, Motif, SiteSettings


class ReportsAndCsvTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username='rep_admin', password='Password123!', role=User.Role.ADMIN
        )

    def test_statistics_origin_motifs_stale_registered_and_csv(self):
        today = timezone.localdate()
        unavail_m = Motif.objects.filter(type=Motif.Type.UNAVAILABLE, is_active=True).first()
        nv_m = Motif.objects.filter(type=Motif.Type.NOT_VERIFIED, is_active=True).first()

        # 1. Programmé vérifié
        c1 = Container.all_objects.create(
            container_number='CMAU9000001',
            origin=Container.Origin.PROGRAMME,
            scheduled_date=today,
            status=Container.Status.CONFIRMED,
        )
        c1.set_verification(Container.Verification.VERIFIED, acting_user=self.admin)

        # 2. Arrivé sans liste, mis sur liste et Non vérifié avec motif
        c2 = Container.all_objects.create(
            container_number='CMAU9000002',
            origin=Container.Origin.ARRIVE_SANS_LISTE,
            scheduled_date=today,
            status=Container.Status.CONFIRMED,
        )
        c2.set_verification(
            Container.Verification.NOT_VERIFIED,
            acting_user=self.admin,
            reason=nv_m,
            note='Scellé absent',
        )

        # 3. Indisponible avec motif
        Container.all_objects.create(
            container_number='CMAU9000003',
            origin=Container.Origin.PROGRAMME,
            scheduled_date=today,
            status=Container.Status.UNAVAILABLE,
            unavailability_reason=unavail_m,
            unavailability_note='Note indispo',
        )

        # 4. Archivé -> exclu des stats et du CSV
        arch = Container.all_objects.create(
            container_number='CMAU9000004',
            origin=Container.Origin.PROGRAMME,
            scheduled_date=today,
            status=Container.Status.PENDING,
            archived_at=timezone.now(),
        )

        # 5. Enregistré sans liste depuis 5 jours (> défaut 3 jours)
        SiteSettings.get_solo()
        stale = Container.all_objects.create(
            container_number='CMAU9000005',
            origin=Container.Origin.ARRIVE_SANS_LISTE,
            status=Container.Status.REGISTERED,
            scheduled_date=None,
            arrived_at=timezone.now() - datetime.timedelta(days=5),
        )

        self.client.force_login(self.admin)
        resp = self.client.get(reverse('terminal:statistics'), {'periode': 'semaine', 'ref': today.isoformat()})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['totals']['scheduled'], 3)
        self.assertContains(resp, escape(nv_m.label))
        self.assertContains(resp, escape(unavail_m.label))
        self.assertContains(resp, stale.container_number)
        self.assertNotContains(resp, arch.container_number)

        # Export CSV
        csv_resp = self.client.get(
            reverse('terminal:container_export_csv'),
            {'date': today.isoformat()},
        )
        self.assertEqual(csv_resp.status_code, 200)
        body = csv_resp.content.decode('utf-8-sig')
        self.assertIn('Origine', body)
        self.assertIn('Motif Non-Verification', body)
        self.assertIn('Precision Indisponibilite', body)
        self.assertIn(nv_m.label, body)
        self.assertIn('Scellé absent', body)
        self.assertNotIn(arch.container_number, body)
