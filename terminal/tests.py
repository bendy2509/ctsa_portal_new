from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import User
from terminal.models import (
    ShippingLine,
    ContainerPrefix,
    UnavailabilityReason,
    Container,
    ContainerStatusLog,
)

# Create your tests here.


class CTSAPortalBusinessTests(TestCase):
    def setUp(self):
        self.client_http = Client()
        self.cma = ShippingLine.objects.create(name='CMA CGM', code='CMA')
        self.antillean = ShippingLine.objects.create(name='Antillean Marine', code='ANT')
        self.reason_stacker = UnavailabilityReason.objects.create(
            code='panne-reach-stacker', label='Panne reach stacker'
        )

        self.admin_user = User.objects.create_user(
            username='admin_test', password='Password123!', role=User.Role.ADMIN
        )
        self.evening_sup = User.objects.create_user(
            username='evening_test', password='Password123!', role=User.Role.EVENING_SUPERVISOR
        )
        self.morning_sup = User.objects.create_user(
            username='morning_test', password='Password123!', role=User.Role.MORNING_SUPERVISOR
        )
        self.client_user = User.objects.create_user(
            username='client_test', password='Password123!', role=User.Role.CLIENT
        )

    def test_manual_shipping_line_assignment_and_status_log_creation(self):
        # The supervisor explicitly assigns the shipping line (e.g. Antillean even if prefix is AMLU/CMAU)
        c = Container(
            container_number='amlu 4052450',
            shipping_line=self.antillean,
            scheduled_date=timezone.localdate(),
            status=Container.Status.PENDING,
        )
        c.save(acting_user=self.evening_sup)

        self.assertEqual(c.container_number, 'AMLU4052450')
        self.assertEqual(c.shipping_line, self.antillean)
        self.assertEqual(ContainerStatusLog.objects.filter(container=c).count(), 1)

        # Change status to Unavailable with reason
        c.status = Container.Status.UNAVAILABLE
        c.unavailability_reason = self.reason_stacker
        c.unavailability_note = 'Intervention en cours'
        c.save(acting_user=self.admin_user)

        logs = list(ContainerStatusLog.objects.filter(container=c).order_by('changed_at'))
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[1].old_status, Container.Status.PENDING)
        self.assertEqual(logs[1].new_status, Container.Status.UNAVAILABLE)
        self.assertIn('Panne reach stacker', logs[1].reason_text)

    def test_morning_supervisor_forced_to_pending_status_and_cannot_delete(self):
        self.client_http.login(username='morning_test', password='Password123!')
        response = self.client_http.post(
            reverse('terminal:container_add_single'),
            {
                'container_number': 'CMAU4052450',
                'shipping_line': str(self.cma.id),
                'status': Container.Status.CONFIRMED,  # Attempt to add as confirmed
                'scheduled_date': timezone.localdate().isoformat(),
            },
        )
        self.assertEqual(response.status_code, 302)
        c = Container.objects.get(container_number='CMAU4052450')
        self.assertEqual(c.status, Container.Status.PENDING)
        self.assertEqual(c.shipping_line, self.cma)

        del_resp = self.client_http.post(
            reverse('terminal:container_delete', args=[c.pk])
        )
        self.assertEqual(del_resp.status_code, 302)
        self.assertTrue(Container.objects.filter(pk=c.pk).exists())

    def test_public_verification_search_and_pdf_export(self):
        c = Container(
            container_number='CMAU4052450',
            shipping_line=self.cma,
            scheduled_date=timezone.localdate(),
            status=Container.Status.CONFIRMED,
        )
        c.save(acting_user=self.evening_sup)

        response = self.client_http.get(
            reverse('terminal:verification'), {'q': 'cmau-4052450'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'CMAU4052450')
        self.assertContains(response, 'Confirmé')
        self.assertContains(response, 'CMA CGM')

        # Test PDF export for confirmed containers
        self.client_http.login(username='evening_test', password='Password123!')
        pdf_resp = self.client_http.get(
            reverse('terminal:container_export_pdf'),
            {'date': timezone.localdate().isoformat(), 'status': 'confirmed'},
        )
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertEqual(pdf_resp['Content-Type'], 'application/pdf')
        self.assertTrue(pdf_resp.content.startswith(b'%PDF'))

    def test_past_confirmed_schedule_is_not_shown_as_valid_today(self):
        import datetime
        today = timezone.localdate()
        Container.objects.create(
            container_number='CMAU4052450',
            shipping_line=self.cma,
            scheduled_date=today - datetime.timedelta(days=7),
            status=Container.Status.CONFIRMED,
        )
        response = self.client_http.get(reverse('terminal:verification'), {'q': 'CMAU4052450'})
        self.assertEqual(response.context['result'].timing, 'past')
        self.assertContains(response, "Non programmé aujourd'hui")
        self.assertNotContains(response, 'Déplacement autorisé')
        self.assertNotContains(response, 'Vous pouvez vous présenter')
        self.assertNotContains(response, 'status-confirmed')

        # La liste « Mes conteneurs » ne doit pas non plus afficher le badge vert
        self.client_http.login(username='client_test', password='Password123!')
        self.client_http.post(
            reverse('terminal:client_watchlist'), {'container_number': 'CMAU4052450'}
        )
        wl = self.client_http.get(reverse('terminal:client_watchlist'))
        self.assertContains(wl, "Non programmé aujourd'hui")
        self.assertNotContains(wl, 'status-confirmed')

    def test_schedule_priority_today_then_next_upcoming_then_last_past(self):
        import datetime
        today = timezone.localdate()
        for offset, status in (
            (-3, Container.Status.CONFIRMED),
            (1, Container.Status.PENDING),
            (5, Container.Status.PENDING),
        ):
            Container.objects.create(
                container_number='CMAU4052450',
                shipping_line=self.cma,
                scheduled_date=today + datetime.timedelta(days=offset),
                status=status,
            )
        url = reverse('terminal:verification')
        response = self.client_http.get(url, {'q': 'CMAU4052450'})
        result = response.context['result']
        self.assertEqual(result.timing, 'upcoming')
        self.assertEqual(result.scheduled_date, today + datetime.timedelta(days=1))
        self.assertContains(response, "Ne pas se déplacer aujourd'hui")
        self.assertNotContains(response, 'status-pending')

        Container.objects.create(
            container_number='CMAU4052450',
            shipping_line=self.cma,
            scheduled_date=today,
            status=Container.Status.CONFIRMED,
        )
        response = self.client_http.get(url, {'q': 'CMAU4052450'})
        self.assertEqual(response.context['result'].timing, 'today')
        self.assertContains(response, 'Déplacement autorisé')

    def test_shipping_lines_add_rename_delete_and_dashboard_quick_actions(self):
        self.client_http.login(username='admin_test', password='Password123!')
        # Add shipping line with only name
        add_resp = self.client_http.post(
            reverse('terminal:shipping_lines'),
            {'action': 'save_line', 'name': 'Accordia'},
        )
        self.assertEqual(add_resp.status_code, 302)
        accordia = ShippingLine.objects.get(name='Accordia')

        # Rename shipping line
        rename_resp = self.client_http.post(
            reverse('terminal:shipping_lines'),
            {'action': 'save_line', 'line_id': str(accordia.id), 'name': 'Accordia Shipping'},
        )
        self.assertEqual(rename_resp.status_code, 302)
        accordia.refresh_from_db()
        self.assertEqual(accordia.name, 'Accordia Shipping')

        # Verify shipping lines page does not display phone/email/address columns
        page_resp = self.client_http.get(reverse('terminal:shipping_lines'))
        self.assertEqual(page_resp.status_code, 200)
        self.assertContains(page_resp, 'Accordia Shipping')
        self.assertNotContains(page_resp, 'contact_email')
        self.assertNotContains(page_resp, 'contact_phone')
        self.assertNotContains(page_resp, 'address')

        # Create a container and test 1-click status update & shipping line update on dashboard
        c = Container.objects.create(
            container_number='ASJU2190841',
            shipping_line=accordia,
            scheduled_date=timezone.localdate(),
            status=Container.Status.PENDING,
        )
        confirm_resp = self.client_http.post(
            reverse('terminal:container_update_status', args=[c.pk]),
            {'status': Container.Status.CONFIRMED},
        )
        self.assertEqual(confirm_resp.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.status, Container.Status.CONFIRMED)
        self.assertEqual(c.shipping_line, accordia)


