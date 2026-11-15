from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import User
from terminal.models import (
    ShippingLine,
    ContainerPrefix,
    Container,
    ContainerStatusLog,
)

# Create your tests here.


class CTSAPortalBusinessTests(TestCase):
    def setUp(self):
        self.client_http = Client()
        self.cma = ShippingLine.objects.create(name='CMA CGM', code='CMA')
        self.antillean = ShippingLine.objects.create(name='Antillean Marine', code='ANT')

        self.admin_user = User.objects.create_user(
            username='admin_test', password='Password123!', role=User.Role.ADMIN
        )
        self.supervisor = User.objects.create_user(
            username='sup_test', password='Password123!', role=User.Role.SUPERVISOR
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
        c.save(acting_user=self.supervisor)

        self.assertEqual(c.container_number, 'AMLU4052450')
        self.assertEqual(c.shipping_line, self.antillean)
        self.assertEqual(ContainerStatusLog.objects.filter(container=c).count(), 1)

        # Change status to Unavailable with reason note
        c.status = Container.Status.UNAVAILABLE
        c.unavailability_note = 'Intervention en cours'
        c.save(acting_user=self.admin_user)

        logs = list(ContainerStatusLog.objects.filter(container=c).order_by('changed_at'))
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[1].old_status, Container.Status.PENDING)
        self.assertEqual(logs[1].new_status, Container.Status.UNAVAILABLE)
        self.assertIn('Intervention en cours', logs[1].reason_text)

    def test_supervisor_forced_to_pending_on_add_and_has_compiled_rights(self):
        self.client_http.login(username='sup_test', password='Password123!')
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

        # Le superviseur unifié peut confirmer un conteneur arrivé sur la rampe et supprimer si besoin.
        self.client_http.post(
            reverse('terminal:container_update_status', args=[c.pk]),
            {'status': Container.Status.CONFIRMED},
        )
        c.refresh_from_db()
        self.assertEqual(c.status, Container.Status.CONFIRMED)

        del_resp = self.client_http.post(
            reverse('terminal:container_delete', args=[c.pk])
        )
        self.assertEqual(del_resp.status_code, 302)
        self.assertFalse(Container.objects.filter(pk=c.pk).exists())

    def test_public_verification_search_and_pdf_export(self):
        c = Container(
            container_number='CMAU4052450',
            shipping_line=self.cma,
            scheduled_date=timezone.localdate(),
            status=Container.Status.CONFIRMED,
        )
        c.save(acting_user=self.supervisor)

        response = self.client_http.get(
            reverse('terminal:verification'), {'q': 'cmau-4052450'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'CMAU4052450')
        self.assertContains(response, 'Confirmé')
        self.assertContains(response, 'CMA CGM')

        # Test PDF export for confirmed containers
        self.client_http.login(username='sup_test', password='Password123!')
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

    def test_bulk_status_update_and_confirm_all_pending(self):
        self.client_http.login(username='sup_test', password='Password123!')
        today = timezone.localdate()
        c1 = Container.objects.create(
            container_number='CMAU1111111',
            shipping_line=self.cma,
            scheduled_date=today,
            status=Container.Status.PENDING,
        )
        c2 = Container.objects.create(
            container_number='CMAU2222222',
            shipping_line=self.cma,
            scheduled_date=today,
            status=Container.Status.PENDING,
        )
        # 1. Confirm all pending of the day in one click
        resp_all = self.client_http.post(
            reverse('terminal:container_status_bulk'),
            {'date': today.isoformat(), 'scope': 'all_pending', 'status': Container.Status.CONFIRMED},
        )
        self.assertEqual(resp_all.status_code, 302)
        c1.refresh_from_db()
        c2.refresh_from_db()
        self.assertEqual(c1.status, Container.Status.CONFIRMED)
        self.assertEqual(c2.status, Container.Status.CONFIRMED)

        # 2. Mark selected container as unavailable with reason note
        resp_unavail = self.client_http.post(
            reverse('terminal:container_status_bulk'),
            {
                'date': today.isoformat(),
                'ids': [str(c1.pk)],
                'status': Container.Status.UNAVAILABLE,
                'unavailability_note': 'En maintenance',
            },
        )
        self.assertEqual(resp_unavail.status_code, 302)
        c1.refresh_from_db()
        c2.refresh_from_db()
        self.assertEqual(c1.status, Container.Status.UNAVAILABLE)
        self.assertEqual(c1.unavailability_note, 'En maintenance')
        self.assertEqual(c2.status, Container.Status.CONFIRMED)

    def test_container_list_detail_edit_delete_and_permissions(self):
        from accounts.models import User
        today = timezone.localdate()
        c = Container.objects.create(
            container_number='MSCU3333333',
            shipping_line=self.cma,
            scheduled_date=today,
            status=Container.Status.PENDING,
        )
        c_dup = Container.objects.create(
            container_number='MSCU4444444',
            shipping_line=self.cma,
            scheduled_date=today,
            status=Container.Status.CONFIRMED,
        )

        # Non-staff user is redirected away from container detail & edit
        client_user = User.objects.create_user(
            username='client_only',
            password='Password123!',
            role=User.Role.CLIENT if hasattr(User, 'Role') else 'client',
        )
        self.client_http.login(username='client_only', password='Password123!')
        self.assertEqual(
            self.client_http.get(reverse('terminal:container_detail', args=[c.pk])).status_code,
            302,
        )
        self.assertEqual(
            self.client_http.get(reverse('terminal:container_edit', args=[c.pk])).status_code,
            302,
        )

        # Staff user can view list without portal-dropdown, view detail, edit, and delete
        self.client_http.login(username='sup_test', password='Password123!')
        list_resp = self.client_http.get(reverse('terminal:containers'))
        self.assertEqual(list_resp.status_code, 200)
        self.assertContains(list_resp, 'MSCU3333333')
        self.assertNotContains(list_resp, 'portal-dropdown')

        detail_resp = self.client_http.get(reverse('terminal:container_detail', args=[c.pk]))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, 'MSCU3333333')

        edit_get = self.client_http.get(reverse('terminal:container_edit', args=[c.pk]))
        self.assertEqual(edit_get.status_code, 200)
        self.assertContains(edit_get, 'portal-has-form-actions')

        # Duplicate container_number on same date returns 200 with error
        edit_dup = self.client_http.post(
            reverse('terminal:container_edit', args=[c.pk]),
            {
                'container_number': 'MSCU4444444',
                'scheduled_date': today.isoformat(),
                'shipping_line': str(self.cma.pk),
                'status': Container.Status.CONFIRMED,
                'unavailability_note': '',
            },
        )
        self.assertEqual(edit_dup.status_code, 200)
        c.refresh_from_db()
        self.assertEqual(c.container_number, 'MSCU3333333')

        # Valid edit updates container and redirects to detail
        edit_ok = self.client_http.post(
            reverse('terminal:container_edit', args=[c.pk]),
            {
                'container_number': 'MSCU-5555555',
                'scheduled_date': today.isoformat(),
                'shipping_line': str(self.cma.pk),
                'status': Container.Status.UNAVAILABLE,
                'unavailability_note': 'Bloqué douane',
            },
        )
        self.assertRedirects(edit_ok, reverse('terminal:container_detail', args=[c.pk]))
        c.refresh_from_db()
        self.assertEqual(c.container_number, 'MSCU5555555')
        self.assertEqual(c.status, Container.Status.UNAVAILABLE)
        self.assertEqual(c.unavailability_note, 'Bloqué douane')

        # Delete removes container and redirects to containers list
        del_resp = self.client_http.post(reverse('terminal:container_delete', args=[c.pk]))
        self.assertEqual(del_resp.status_code, 302)
        self.assertFalse(Container.objects.filter(pk=c.pk).exists())

        # Adding without shipping_line is rejected when active shipping lines exist
        no_line_resp = self.client_http.post(
            reverse('terminal:container_add_single'),
            {'container_number': 'MSCU7777777', 'scheduled_date': today.isoformat()},
        )
        self.assertEqual(no_line_resp.status_code, 302)
        self.assertFalse(Container.objects.filter(container_number='MSCU7777777').exists())


class ShippingLinesAndStatisticsTests(TestCase):
    def setUp(self):
        self.client_http = Client()
        self.admin_user = User.objects.create_user(
            username='sl_admin', password='Password123!', role=User.Role.ADMIN
        )
        self.client_user = User.objects.create_user(
            username='sl_client', password='Password123!', role=User.Role.CLIENT
        )

    def test_shipping_line_create_edit_delete_and_permissions(self):
        # Non-admin cannot access create/edit/delete
        self.client_http.force_login(self.client_user)
        resp_forbidden = self.client_http.get(reverse('terminal:shipping_line_create'))
        self.assertIn(resp_forbidden.status_code, (302, 403))

        # Admin creates a shipping line
        self.client_http.force_login(self.admin_user)
        create_resp = self.client_http.post(
            reverse('terminal:shipping_line_create'),
            {'name': 'Maersk Line', 'code': 'MSK', 'is_active': 'on'},
        )
        self.assertRedirects(create_resp, reverse('terminal:shipping_lines'))
        line = ShippingLine.objects.get(name='Maersk Line')
        self.assertEqual(line.code, 'MAERSK-LINE')
        self.assertTrue(line.is_active)

        # Non-admin cannot edit or delete existing line
        self.client_http.force_login(self.client_user)
        self.assertIn(
            self.client_http.get(reverse('terminal:shipping_line_edit', args=[line.pk])).status_code,
            (302, 403),
        )
        self.assertIn(
            self.client_http.post(reverse('terminal:shipping_line_delete', args=[line.pk])).status_code,
            (302, 403),
        )
        self.assertTrue(ShippingLine.objects.filter(pk=line.pk).exists())

        # Admin edits and deletes the shipping line
        self.client_http.force_login(self.admin_user)
        edit_resp = self.client_http.post(
            reverse('terminal:shipping_line_edit', args=[line.pk]),
            {'name': 'Maersk Haiti'},
        )
        self.assertRedirects(edit_resp, reverse('terminal:shipping_lines'))
        line.refresh_from_db()
        self.assertEqual(line.name, 'Maersk Haiti')
        self.assertEqual(line.code, 'MAERSK-HAITI')

        del_resp = self.client_http.post(reverse('terminal:shipping_line_delete', args=[line.pk]))
        self.assertRedirects(del_resp, reverse('terminal:shipping_lines'))
        self.assertFalse(ShippingLine.objects.filter(pk=line.pk).exists())

    def test_statistics_french_dates_csv_and_show_lines(self):
        today = timezone.localdate()
        Container.objects.create(
            container_number='CMAU1111111',
            scheduled_date=today,
            status=Container.Status.CONFIRMED,
            created_by=self.admin_user,
        )
        self.client_http.force_login(self.admin_user)

        # Only unnamed container -> show_lines is False
        du_fr = today.strftime('%d/%m/%Y')
        resp = self.client_http.get(
            reverse('terminal:statistics'),
            {'periode': 'perso', 'du': du_fr, 'au': du_fr},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['show_lines'])

        # CSV export uses unified "Restent a verifier" header
        csv_resp = self.client_http.get(
            reverse('terminal:statistics'),
            {'periode': 'perso', 'du': du_fr, 'au': du_fr, 'export': 'csv'},
        )
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn('Restent a verifier', csv_resp.content.decode('utf-8-sig'))

        # Adding a container with a shipping line turns show_lines True
        cma = ShippingLine.objects.create(name='CMA CGM', code='CMA')
        Container.objects.create(
            container_number='CMAU2222222',
            scheduled_date=today,
            shipping_line=cma,
            status=Container.Status.CONFIRMED,
            created_by=self.admin_user,
        )
        resp_with_line = self.client_http.get(
            reverse('terminal:statistics'),
            {'periode': 'perso', 'du': du_fr, 'au': du_fr},
        )
        self.assertEqual(resp_with_line.status_code, 200)
        self.assertTrue(resp_with_line.context['show_lines'])

    def test_site_settings_edit_and_public_rendering(self):
        # Non-admin cannot access site settings
        self.client_http.force_login(self.client_user)
        resp_forbidden = self.client_http.get(reverse('terminal:site_settings'))
        self.assertIn(resp_forbidden.status_code, (302, 403))

        # Admin views and updates site settings
        self.client_http.force_login(self.admin_user)
        get_resp = self.client_http.get(reverse('terminal:site_settings'))
        self.assertEqual(get_resp.status_code, 200)
        self.assertContains(get_resp, 'contact@bendyservilus.com')
        self.assertContains(get_resp, '+50941705257')

        post_resp = self.client_http.post(
            reverse('terminal:site_settings'),
            {
                'company_name': 'Cap Terminal S.A.',
                'portal_title': 'Portail RAMPE',
                'portal_subtitle': 'Vérification douanière des conteneurs',
                'location_label': 'Port du Cap-Haïtien',
                'address_line': 'Boulevard du Port, Cap-Haïtien',
                'contact_email': 'rampe@bendyservilus.com',
                'contact_phone_primary': '+50941705257',
                'contact_phone_secondary': '',
                'footer_about': 'Terminal certifié ISPS.',
                'hero_badge': 'Cap-Haïtien · RAMPE',
                'hero_title': 'Titre personnalisé RAMPE',
                'hero_subtitle': 'Sous-titre personnalisé.',
                'search_note': 'Mise à jour quotidienne.',
                'status_guide_title': 'Statuts RAMPE',
                'status_guide_subtitle': 'Explication des statuts.',
                'confirmed_instruction': 'Venez avec vos documents.',
                'pending_instruction': 'Patientez.',
                'unavailable_instruction': 'Contactez votre transitaire.',
                'client_cta_title': 'Suivi multi-conteneurs',
                'client_cta_subtitle': 'Créez un compte client.',
            },
        )
        self.assertRedirects(post_resp, reverse('terminal:site_settings'))

        # Public home renders the updated settings
        self.client_http.logout()
        home_resp = self.client_http.get(reverse('terminal:home'))
        self.assertEqual(home_resp.status_code, 200)
        self.assertContains(home_resp, 'rampe@bendyservilus.com')
        self.assertContains(home_resp, '+50941705257')
        self.assertContains(home_resp, 'Titre personnalisé RAMPE')

    def test_pwa_manifest_and_service_worker(self):
        manifest_resp = self.client_http.get(reverse('terminal:pwa_manifest'))
        self.assertEqual(manifest_resp.status_code, 200)
        self.assertIn('application/manifest+json', manifest_resp['Content-Type'])
        data = manifest_resp.json()
        self.assertEqual(data['start_url'], '/')
        self.assertEqual(data['display'], 'standalone')
        self.assertGreaterEqual(len(data['icons']), 3)

        sw_resp = self.client_http.get(reverse('terminal:pwa_sw'))
        self.assertEqual(sw_resp.status_code, 200)
        self.assertIn('application/javascript', sw_resp['Content-Type'])
        self.assertIn('ctsa-rampe-v1', sw_resp.content.decode('utf-8'))







