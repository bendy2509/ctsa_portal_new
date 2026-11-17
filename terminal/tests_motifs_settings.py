from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from terminal.models import Motif, PortalAuditLog, SiteSettings


class MotifsAndSettingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superadmin = User.objects.create_user(
            username='ms_superadmin',
            password='Password123!',
            role=User.Role.ADMIN,
            is_superuser=True,
            is_staff=True,
        )
        cls.admin = User.objects.create_user(
            username='ms_admin',
            password='Password123!',
            role=User.Role.ADMIN,
        )

    def test_seeded_motifs_and_historical_not_selectable(self):
        unavail = Motif.objects.filter(type=Motif.Type.UNAVAILABLE, is_active=True)
        not_verif = Motif.objects.filter(type=Motif.Type.NOT_VERIFIED, is_active=True)
        hist = Motif.objects.get(type=Motif.Type.NOT_VERIFIED, label="Non renseigné (historique)")
        self.assertEqual(unavail.count(), 5)
        self.assertEqual(not_verif.count(), 6)
        self.assertFalse(hist.is_active)
        self.assertTrue(hist.is_system)

    def test_admin_gets_403_on_motifs_and_settings(self):
        motif = Motif.objects.filter(is_system=False).first()
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('terminal:motif_create'), {'type': 'unavailable', 'label': 'Test'}).status_code, 403)
        self.assertEqual(self.client.post(reverse('terminal:motif_toggle', args=[motif.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('terminal:motif_update', args=[motif.pk]), {'display_order': 2}).status_code, 403)

    def test_superadmin_manages_motifs_and_windows_with_audit_log(self):
        self.client.force_login(self.superadmin)
        resp = self.client.post(
            reverse('terminal:motif_create'),
            {'type': 'unavailable', 'label': 'Panne portique', 'display_order': 2, 'requires_note': 'on'},
        )
        self.assertRedirects(resp, reverse('terminal:site_settings'))
        created = Motif.objects.get(label='Panne portique')
        self.assertTrue(created.requires_note)

        self.client.post(reverse('terminal:motif_toggle', args=[created.pk]))
        created.refresh_from_db()
        self.assertFalse(created.is_active)

        self.client.post(reverse('terminal:motif_update', args=[created.pk]), {'display_order': 9})
        created.refresh_from_db()
        self.assertEqual(created.display_order, 9)

        # Système non modifiable
        hist = Motif.objects.get(label="Non renseigné (historique)")
        self.client.post(reverse('terminal:motif_toggle', args=[hist.pk]))
        hist.refresh_from_db()
        self.assertFalse(hist.is_active)

        # Validation des bornes sur SiteSettings
        site_obj = SiteSettings.get_solo()
        bad_resp = self.client.post(
            reverse('terminal:site_settings'),
            {
                'company_name': site_obj.company_name,
                'portal_title': site_obj.portal_title,
                'portal_subtitle': site_obj.portal_subtitle,
                'location_label': site_obj.location_label,
                'address_line': site_obj.address_line,
                'contact_email': site_obj.contact_email,
                'contact_phone_primary': site_obj.contact_phone_primary,
                'footer_about': site_obj.footer_about,
                'hero_title': site_obj.hero_title,
                'hero_subtitle': site_obj.hero_subtitle,
                'search_note': site_obj.search_note,
                'step1_title': site_obj.step1_title,
                'step1_text': site_obj.step1_text,
                'step2_title': site_obj.step2_title,
                'step2_text': site_obj.step2_text,
                'step3_title': site_obj.step3_title,
                'step3_text': site_obj.step3_text,
                'status_guide_title': site_obj.status_guide_title,
                'status_guide_subtitle': site_obj.status_guide_subtitle,
                'confirmed_title': site_obj.confirmed_title,
                'confirmed_description': site_obj.confirmed_description,
                'confirmed_instruction': site_obj.confirmed_instruction,
                'pending_title': site_obj.pending_title,
                'pending_description': site_obj.pending_description,
                'pending_instruction': site_obj.pending_instruction,
                'unavailable_title': site_obj.unavailable_title,
                'unavailable_description': site_obj.unavailable_description,
                'unavailable_instruction': site_obj.unavailable_instruction,
                'client_cta_title': site_obj.client_cta_title,
                'client_cta_subtitle': site_obj.client_cta_subtitle,
                'hero_carousel_limit': '4',
                'correction_window_minutes': '9999',
                'delete_window_hours': '24',
                'unscheduled_alert_days': '3',
            },
        )
        self.assertEqual(bad_resp.status_code, 200)
        site_obj.refresh_from_db()
        self.assertEqual(site_obj.correction_window_minutes, 60)

        ok_resp = self.client.post(
            reverse('terminal:site_settings'),
            {
                'company_name': site_obj.company_name,
                'portal_title': site_obj.portal_title,
                'portal_subtitle': site_obj.portal_subtitle,
                'location_label': site_obj.location_label,
                'address_line': site_obj.address_line,
                'contact_email': site_obj.contact_email,
                'contact_phone_primary': site_obj.contact_phone_primary,
                'footer_about': site_obj.footer_about,
                'hero_title': site_obj.hero_title,
                'hero_subtitle': site_obj.hero_subtitle,
                'search_note': site_obj.search_note,
                'step1_title': site_obj.step1_title,
                'step1_text': site_obj.step1_text,
                'step2_title': site_obj.step2_title,
                'step2_text': site_obj.step2_text,
                'step3_title': site_obj.step3_title,
                'step3_text': site_obj.step3_text,
                'status_guide_title': site_obj.status_guide_title,
                'status_guide_subtitle': site_obj.status_guide_subtitle,
                'confirmed_title': site_obj.confirmed_title,
                'confirmed_description': site_obj.confirmed_description,
                'confirmed_instruction': site_obj.confirmed_instruction,
                'pending_title': site_obj.pending_title,
                'pending_description': site_obj.pending_description,
                'pending_instruction': site_obj.pending_instruction,
                'unavailable_title': site_obj.unavailable_title,
                'unavailable_description': site_obj.unavailable_description,
                'unavailable_instruction': site_obj.unavailable_instruction,
                'client_cta_title': site_obj.client_cta_title,
                'client_cta_subtitle': site_obj.client_cta_subtitle,
                'hero_carousel_limit': '4',
                'correction_window_minutes': '45',
                'delete_window_hours': '12',
                'unscheduled_alert_days': '5',
            },
        )
        self.assertRedirects(ok_resp, reverse('terminal:site_settings'))
        site_obj.refresh_from_db()
        self.assertEqual(site_obj.correction_window_minutes, 45)
        self.assertEqual(site_obj.delete_window_hours, 12)
        self.assertEqual(site_obj.unscheduled_alert_days, 5)
        self.assertTrue(
            PortalAuditLog.objects.filter(
                category=PortalAuditLog.Category.SETTING_CHANGE,
                target_label='SiteSettings.correction_window_minutes',
                old_value='60',
                new_value='45',
            ).exists()
        )
