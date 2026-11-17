import datetime
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from terminal.models import Container, Motif, VerificationEvent


class InspectionAndArchiveWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superadmin = User.objects.create_user(
            username='iw_superadmin',
            password='Password123!',
            role=User.Role.ADMIN,
            is_superuser=True,
            is_staff=True,
        )
        cls.admin = User.objects.create_user(
            username='iw_admin', password='Password123!', role=User.Role.ADMIN
        )
        cls.supervisor = User.objects.create_user(
            username='iw_sup', password='Password123!', role=User.Role.SUPERVISOR
        )
        cls.agent1 = User.objects.create_user(
            username='iw_agent1', password='Password123!', role=User.Role.AGENT
        )
        cls.agent2 = User.objects.create_user(
            username='iw_agent2', password='Password123!', role=User.Role.AGENT
        )

    def test_refuses_not_verified_and_unavailable_without_motif_or_autre_without_note(self):
        today = timezone.localdate()
        c = Container.all_objects.create(
            container_number='CMAU7000001',
            scheduled_date=today,
            status=Container.Status.CONFIRMED,
        )
        autre_nv = Motif.objects.get(type=Motif.Type.NOT_VERIFIED, label="Autre (précisez)")
        std_nv = Motif.objects.filter(type=Motif.Type.NOT_VERIFIED, requires_note=False, is_active=True).first()
        autre_un = Motif.objects.get(type=Motif.Type.UNAVAILABLE, label="Autre (précisez)")

        # Indisponible sans motif -> refusé
        self.client.force_login(self.supervisor)
        self.client.post(
            reverse('terminal:container_update_status', args=[c.pk]),
            {'status': Container.Status.UNAVAILABLE, 'unavailability_note': 'Test'},
        )
        c.refresh_from_db()
        self.assertEqual(c.status, Container.Status.CONFIRMED)

        # Indisponible avec "Autre" sans précision -> refusé
        self.client.post(
            reverse('terminal:container_update_status', args=[c.pk]),
            {'status': Container.Status.UNAVAILABLE, 'unavailability_reason': str(autre_un.pk), 'unavailability_note': ''},
        )
        c.refresh_from_db()
        self.assertEqual(c.status, Container.Status.CONFIRMED)

        # Non vérifié sans motif -> refusé
        self.client.force_login(self.agent1)
        self.client.post(
            reverse('terminal:container_set_verification', args=[c.pk]),
            {'result': Container.Verification.NOT_VERIFIED, 'verification_note': 'Test'},
        )
        c.refresh_from_db()
        self.assertEqual(c.verification_result, '')

        # Non vérifié avec "Autre" sans précision -> refusé
        self.client.post(
            reverse('terminal:container_set_verification', args=[c.pk]),
            {'result': Container.Verification.NOT_VERIFIED, 'verification_reason': str(autre_nv.pk), 'verification_note': ''},
        )
        c.refresh_from_db()
        self.assertEqual(c.verification_result, '')

        # Non vérifié avec motif valide -> accepté
        self.client.post(
            reverse('terminal:container_set_verification', args=[c.pk]),
            {'result': Container.Verification.NOT_VERIFIED, 'verification_reason': str(std_nv.pk)},
        )
        c.refresh_from_db()
        self.assertEqual(c.verification_result, Container.Verification.NOT_VERIFIED)
        self.assertEqual(c.verification_reason, std_nv)

    def test_agent_correction_ownership_and_supervisor_delete_movement(self):
        today = timezone.localdate()
        c = Container.all_objects.create(
            container_number='CMAU7000002',
            scheduled_date=today,
            status=Container.Status.CONFIRMED,
        )
        base_time = timezone.now()
        with patch('django.utils.timezone.now', return_value=base_time):
            self.client.force_login(self.agent1)
            self.client.post(
                reverse('terminal:container_set_verification', args=[c.pk]),
                {'result': Container.Verification.VERIFIED, 'version': str(c.version)},
            )
            c.refresh_from_db()
            self.assertEqual(c.verification_result, Container.Verification.VERIFIED)

        # Agent2 ne peut pas corriger le marquage d'Agent1
        with patch('django.utils.timezone.now', return_value=base_time + datetime.timedelta(minutes=10)):
            self.client.force_login(self.agent2)
            resp = self.client.post(
                reverse('terminal:container_set_verification', args=[c.pk]),
                {'result': '', 'version': str(c.version)},
            )
            self.assertEqual(resp.status_code, 403)

            # Agent1 peut corriger son propre marquage < 60 min
            self.client.force_login(self.agent1)
            self.client.post(
                reverse('terminal:container_set_verification', args=[c.pk]),
                {'result': '', 'version': str(c.version)},
            )
            c.refresh_from_db()
            self.assertEqual(c.verification_result, '')

            # Agent1 re-marque Vérifié à +10 min
            self.client.post(
                reverse('terminal:container_set_verification', args=[c.pk]),
                {'result': Container.Verification.VERIFIED, 'version': str(c.version)},
            )
            c.refresh_from_db()

        # À +80 min (hors fenêtre 60 min) : Agent1 refusé 403, Superviseur supprime le mouvement avec motif
        with patch('django.utils.timezone.now', return_value=base_time + datetime.timedelta(minutes=80)):
            self.client.force_login(self.agent1)
            self.assertEqual(
                self.client.post(
                    reverse('terminal:container_set_verification', args=[c.pk]),
                    {'result': '', 'version': str(c.version)},
                ).status_code,
                403,
            )

            self.client.force_login(self.supervisor)
            # Sans motif -> refusé
            self.client.post(
                reverse('terminal:container_set_verification', args=[c.pk]),
                {'result': '', 'delete_movement': '1', 'archive_reason': '', 'version': str(c.version)},
            )
            c.refresh_from_db()
            self.assertEqual(c.verification_result, Container.Verification.VERIFIED)

            # Avec motif -> mouvement archivé, conteneur revient à Non inspecté (Confirmé)
            self.client.post(
                reverse('terminal:container_set_verification', args=[c.pk]),
                {'result': '', 'delete_movement': '1', 'archive_reason': 'Erreur de saisie', 'version': str(c.version)},
            )
            c.refresh_from_db()
            self.assertEqual(c.status, Container.Status.CONFIRMED)
            self.assertEqual(c.verification_result, '')
            self.assertTrue(
                VerificationEvent.objects.filter(
                    container=c, archived_at__isnull=False, archive_reason='Erreur de saisie'
                ).exists()
            )

    def test_concurrency_conflict_and_superadmin_restore(self):
        today = timezone.localdate()
        c = Container.all_objects.create(
            container_number='CMAU7000003',
            scheduled_date=today,
            status=Container.Status.CONFIRMED,
        )
        stale_version = c.version

        self.client.force_login(self.agent1)
        self.client.post(
            reverse('terminal:container_set_verification', args=[c.pk]),
            {'result': Container.Verification.VERIFIED, 'version': str(stale_version)},
        )
        c.refresh_from_db()
        self.assertEqual(c.verification_result, Container.Verification.VERIFIED)

        # Deuxième inspecteur avec l'ancienne version -> conflit, pas d'écrasement
        nv_motif = Motif.objects.filter(type=Motif.Type.NOT_VERIFIED, requires_note=False, is_active=True).first()
        self.client.force_login(self.supervisor)
        self.client.post(
            reverse('terminal:container_set_verification', args=[c.pk]),
            {
                'result': Container.Verification.NOT_VERIFIED,
                'verification_reason': str(nv_motif.pk),
                'version': str(stale_version),
            },
        )
        c.refresh_from_db()
        self.assertEqual(c.verification_result, Container.Verification.VERIFIED)

        # Archivage + Restauration (Admin -> 403, Superadmin -> 302)
        arch = Container.all_objects.create(
            container_number='CMAU7000004',
            origin=Container.Origin.ARRIVE_SANS_LISTE,
            status=Container.Status.REGISTERED,
            scheduled_date=None,
        )
        arch.archive(acting_user=self.admin, reason='Test archive')
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('terminal:container_restore', args=[arch.pk])).status_code, 403)

        self.client.force_login(self.superadmin)
        resp = self.client.post(reverse('terminal:container_restore', args=[arch.pk]))
        self.assertRedirects(resp, reverse('terminal:status_logs'))
        arch.refresh_from_db()
        self.assertIsNone(arch.archived_at)
