from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from terminal.models import Container


class RegisteredSearchAndReuseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.supervisor = User.objects.create_user(
            username='rs_sup', password='Password123!', role=User.Role.SUPERVISOR
        )
        cls.agent = User.objects.create_user(
            username='rs_agent', password='Password123!', role=User.Role.AGENT
        )

    def test_search_json_permissions_and_filtering(self):
        today = timezone.localdate()
        reg1 = Container.all_objects.create(
            container_number='MSKU1111111',
            origin=Container.Origin.ARRIVE_SANS_LISTE,
            status=Container.Status.REGISTERED,
            scheduled_date=None,
        )
        Container.all_objects.create(
            container_number='MSKU2222222',
            origin=Container.Origin.PROGRAMME,
            status=Container.Status.PENDING,
            scheduled_date=today,
        )
        self.client.force_login(self.agent)
        self.assertEqual(
            self.client.get(reverse('terminal:registered_search_json'), {'q': 'MS'}).status_code,
            403,
        )

        self.client.force_login(self.supervisor)
        resp = self.client.get(
            reverse('terminal:registered_search_json'),
            {'q': 'ms', 'date': today.isoformat()},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['results']
        self.assertEqual([r['id'] for r in data], [reg1.pk])
        self.assertEqual(data[0]['number'], 'MSKU1111111')

    def test_add_registered_to_list_and_reuse_in_single_and_bulk(self):
        today = timezone.localdate()
        r1 = Container.all_objects.create(
            container_number='CMAU5000001',
            origin=Container.Origin.ARRIVE_SANS_LISTE,
            status=Container.Status.REGISTERED,
            scheduled_date=None,
        )
        r2 = Container.all_objects.create(
            container_number='CMAU5000002',
            origin=Container.Origin.ARRIVE_SANS_LISTE,
            status=Container.Status.REGISTERED,
            scheduled_date=None,
        )
        r3 = Container.all_objects.create(
            container_number='CMAU5000003',
            origin=Container.Origin.ARRIVE_SANS_LISTE,
            status=Container.Status.REGISTERED,
            scheduled_date=None,
        )
        self.client.force_login(self.supervisor)

        # 1. Via sélection recherche
        self.client.post(
            reverse('terminal:registered_add_to_list'),
            {'scheduled_date': today.isoformat(), 'ids': [str(r1.pk)]},
        )
        r1.refresh_from_db()
        self.assertEqual(r1.status, Container.Status.PENDING)
        self.assertEqual(r1.origin, Container.Origin.ARRIVE_SANS_LISTE)
        self.assertEqual(r1.scheduled_date, today)

        # 2. Via ajout unitaire (réutilise r2 sans doublon)
        self.client.post(
            reverse('terminal:container_add_single'),
            {'container_number': 'CMAU5000002', 'scheduled_date': today.isoformat()},
        )
        r2.refresh_from_db()
        self.assertEqual(r2.status, Container.Status.PENDING)
        self.assertEqual(r2.origin, Container.Origin.ARRIVE_SANS_LISTE)
        self.assertEqual(Container.all_objects.filter(normalized_number='CMAU5000002').count(), 1)

        # 3. Via ajout en lot (réutilise r3 sans doublon)
        self.client.post(
            reverse('terminal:container_add_bulk'),
            {'container_list': 'CMAU5000003', 'scheduled_date': today.isoformat()},
        )
        r3.refresh_from_db()
        self.assertEqual(r3.status, Container.Status.PENDING)
        self.assertEqual(r3.origin, Container.Origin.ARRIVE_SANS_LISTE)
        self.assertEqual(Container.all_objects.filter(normalized_number='CMAU5000003').count(), 1)
