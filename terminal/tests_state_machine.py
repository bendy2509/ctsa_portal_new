import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from terminal.models import Container, PortalAuditLog
from terminal.views import _carry_into


class StateMachineAndLockingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superadmin = User.objects.create_user(
            username='sm_superadmin',
            password='Password123!',
            role=User.Role.ADMIN,
            is_superuser=True,
            is_staff=True,
        )
        cls.supervisor = User.objects.create_user(
            username='sm_sup',
            password='Password123!',
            role=User.Role.SUPERVISOR,
        )
        cls.agent = User.objects.create_user(
            username='sm_agent',
            password='Password123!',
            role=User.Role.AGENT,
        )

    def test_cannot_inspect_non_confirmed_container(self):
        today = timezone.localdate()
        reg = Container.all_objects.create(
            container_number='CMAU1000001',
            origin=Container.Origin.ARRIVE_SANS_LISTE,
            status=Container.Status.REGISTERED,
            scheduled_date=None,
        )
        pend = Container.all_objects.create(
            container_number='CMAU1000002',
            scheduled_date=today,
            status=Container.Status.PENDING,
        )
        unavail = Container.all_objects.create(
            container_number='CMAU1000003',
            scheduled_date=today,
            status=Container.Status.UNAVAILABLE,
            unavailability_note='Inaccessible',
        )
        for c in (reg, pend, unavail):
            with self.subTest(status=c.status):
                with self.assertRaises(ValidationError):
                    c.set_verification(Container.Verification.VERIFIED, acting_user=self.agent)
                with self.assertRaises(ValidationError):
                    c.set_verification(Container.Verification.NOT_VERIFIED, acting_user=self.agent, note='Test')

    def test_cannot_confirm_or_mark_unavailable_registered_container(self):
        reg = Container.all_objects.create(
            container_number='CMAU1000004',
            origin=Container.Origin.ARRIVE_SANS_LISTE,
            status=Container.Status.REGISTERED,
            scheduled_date=None,
        )
        with self.assertRaises(ValidationError):
            reg.change_status(Container.Status.CONFIRMED, acting_user=self.supervisor)
        with self.assertRaises(ValidationError):
            reg.change_status(Container.Status.UNAVAILABLE, acting_user=self.supervisor, note='Test')

    def test_verified_container_is_locked_against_status_change_and_archive(self):
        today = timezone.localdate()
        c = Container.all_objects.create(
            container_number='CMAU1000005',
            scheduled_date=today,
            status=Container.Status.CONFIRMED,
        )
        c.set_verification(Container.Verification.VERIFIED, acting_user=self.agent)
        c.refresh_from_db()

        for target in (Container.Status.PENDING, Container.Status.UNAVAILABLE, Container.Status.CONFIRMED):
            with self.assertRaises(ValidationError):
                c.change_status(target, acting_user=self.supervisor, note='Tentative')

        with self.assertRaises(ValidationError):
            c.archive(acting_user=self.superadmin, reason='Test')
        with self.assertRaises(ValidationError):
            c.remove_from_list(acting_user=self.superadmin, reason='Test')

        # Via HTTP POST : le conteneur reste Confirmé + Vérifié
        self.client.force_login(self.supervisor)
        self.client.post(
            reverse('terminal:container_update_status', args=[c.pk]),
            {'status': Container.Status.PENDING},
        )
        self.client.post(reverse('terminal:container_delete', args=[c.pk]))
        c.refresh_from_db()
        self.assertEqual(c.status, Container.Status.CONFIRMED)
        self.assertEqual(c.verification_result, Container.Verification.VERIFIED)
        self.assertIsNone(c.archived_at)

    def test_cannot_archive_container_on_list_directly_and_remove_from_list_respects_origin(self):
        today = timezone.localdate()
        prog = Container.all_objects.create(
            container_number='CMAU1000006',
            origin=Container.Origin.PROGRAMME,
            scheduled_date=today,
            status=Container.Status.PENDING,
        )
        with self.assertRaises(ValidationError):
            prog.archive(acting_user=self.superadmin, reason='Direct')

        prog.remove_from_list(acting_user=self.supervisor, reason='Annulation liste')
        prog.refresh_from_db()
        self.assertIsNotNone(prog.archived_at)
        self.assertNotIn(prog, Container.objects.all())

        # Restauration Superadmin
        prog.restore(acting_user=self.superadmin, reason='Erreur')
        prog.refresh_from_db()
        self.assertIsNone(prog.archived_at)
        self.assertTrue(
            PortalAuditLog.objects.filter(
                category=PortalAuditLog.Category.ARCHIVE_RESTORE,
                target_label='Container:CMAU1000006',
            ).exists()
        )

        # Origine arrive_sans_liste : retirer de la liste renvoie à Enregistré
        arr = Container.all_objects.create(
            container_number='CMAU1000007',
            origin=Container.Origin.ARRIVE_SANS_LISTE,
            scheduled_date=today,
            status=Container.Status.PENDING,
        )
        arr.remove_from_list(acting_user=self.supervisor)
        arr.refresh_from_db()
        self.assertEqual(arr.status, Container.Status.REGISTERED)
        self.assertIsNone(arr.scheduled_date)
        self.assertIsNone(arr.archived_at)
        self.assertIn(arr, Container.registered_objects.all())

    def test_carryover_inherits_origin_and_blocks_verification_modification(self):
        yesterday = timezone.localdate() - datetime.timedelta(days=1)
        today = timezone.localdate()
        old = Container.all_objects.create(
            container_number='CMAU1000008',
            origin=Container.Origin.ARRIVE_SANS_LISTE,
            scheduled_date=yesterday,
            status=Container.Status.CONFIRMED,
        )
        old.set_verification(Container.Verification.NOT_VERIFIED, acting_user=self.agent, note='Absent')
        carried = _carry_into(old, today, self.supervisor)
        self.assertEqual(carried.origin, Container.Origin.ARRIVE_SANS_LISTE)
        self.assertEqual(carried.status, Container.Status.PENDING)

        old.refresh_from_db()
        with self.assertRaises(ValidationError):
            old.set_verification('', acting_user=self.supervisor, note='Tentative annulation')

    def test_optimistic_version_conflict_detected(self):
        today = timezone.localdate()
        c = Container.all_objects.create(
            container_number='CMAU1000009',
            scheduled_date=today,
            status=Container.Status.CONFIRMED,
        )
        initial_version = c.version
        c.set_verification(Container.Verification.VERIFIED, acting_user=self.agent, expected_version=initial_version)
        with self.assertRaises(ValidationError):
            c.set_verification(
                Container.Verification.NOT_VERIFIED,
                acting_user=self.supervisor,
                note='Conflit',
                expected_version=initial_version,
            )
