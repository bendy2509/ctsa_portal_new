import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from terminal.models import Container, ContainerStatusLog, ShippingLine
from terminal.views import unverified_backlog


class VerificationTrackingTests(TestCase):
    """Vérification de séance, suivi des non vérifiés, reprise en un clic et statistiques."""

    def setUp(self):
        self.today = timezone.localdate()
        self.yesterday = self.today - datetime.timedelta(days=1)
        self.tomorrow = self.today + datetime.timedelta(days=1)
        self.cma = ShippingLine.objects.create(name='CMA CGM', code='CMA')
        self.other = ShippingLine.objects.create(name='Antillean Marine', code='ANT')
        self.admin = User.objects.create_user(username='adm', password='Password123!', role=User.Role.ADMIN)
        self.evening = User.objects.create_user(
            username='soir', password='Password123!', role=User.Role.EVENING_SUPERVISOR
        )
        self.morning = User.objects.create_user(
            username='matin', password='Password123!', role=User.Role.MORNING_SUPERVISOR
        )
        self.customer = User.objects.create_user(username='cli', password='Password123!', role=User.Role.CLIENT)

    def make(self, number, date, status=Container.Status.CONFIRMED, line=None):
        return Container.objects.create(
            container_number=number, scheduled_date=date, status=status, shipping_line=line or self.cma
        )

    def login(self, username):
        self.client.logout()
        self.client.login(username=username, password='Password123!')

    def mark(self, container, result):
        return self.client.post(
            reverse('terminal:container_set_verification', args=[container.pk]), {'result': result}
        )

    def backlog_numbers(self, target):
        return sorted(unverified_backlog(target).values_list('container_number', flat=True))

    def test_three_staff_roles_can_mark_but_client_cannot(self):
        for idx, username in enumerate(['adm', 'soir', 'matin']):
            c = self.make(f'CMAU000000{idx}', self.today)
            self.login(username)
            self.mark(c, 'verified')
            c.refresh_from_db()
            self.assertEqual(c.verification_result, 'verified', username)
            self.assertEqual(c.verified_by.username, username)
            self.assertIsNotNone(c.verified_at)

        c = self.make('CMAU0000009', self.today)
        self.login('cli')
        self.mark(c, 'verified')
        c.refresh_from_db()
        self.assertEqual(c.verification_result, '')

    def test_only_confirmed_started_sessions_can_be_marked(self):
        self.login('adm')
        pending = self.make('CMAU0000001', self.today, Container.Status.PENDING)
        future = self.make('CMAU0000002', self.tomorrow)
        for c in (pending, future):
            self.mark(c, 'verified')
            c.refresh_from_db()
            self.assertEqual(c.verification_result, '')

    def test_leaving_confirmed_clears_verification_and_logs_are_written(self):
        self.login('adm')
        c = self.make('CMAU0000001', self.today)
        self.mark(c, 'not_verified')
        c.refresh_from_db()
        self.assertEqual(c.verification_result, 'not_verified')
        self.assertTrue(
            ContainerStatusLog.objects.filter(container=c, action='not_verified').exists()
        )
        c.status = Container.Status.PENDING
        c.save(acting_user=self.admin)
        c.refresh_from_db()
        self.assertEqual(c.verification_result, '')
        self.assertIsNone(c.verified_at)

    def test_bulk_marks_remaining_confirmed(self):
        self.login('matin')
        done = self.make('CMAU0000001', self.today)
        done.set_verification('not_verified', acting_user=self.admin)
        left = self.make('CMAU0000002', self.today)
        pending = self.make('CMAU0000003', self.today, Container.Status.PENDING)
        self.client.post(reverse('terminal:container_verify_bulk'), {
            'date': self.today.isoformat(), 'scope': 'remaining', 'result': 'verified',
        })
        for c in (done, left, pending):
            c.refresh_from_db()
        self.assertEqual(done.verification_result, 'not_verified')
        self.assertEqual(left.verification_result, 'verified')
        self.assertEqual(pending.verification_result, '')

    def test_backlog_keeps_every_unverified_container_whatever_its_status(self):
        self.make('CMAU0000001', self.yesterday, Container.Status.CONFIRMED)
        self.make('CMAU0000002', self.yesterday, Container.Status.PENDING)
        self.make('CMAU0000003', self.yesterday, Container.Status.UNAVAILABLE)
        verified = self.make('CMAU0000004', self.yesterday)
        verified.set_verification('verified', acting_user=self.admin)
        self.make('CMAU0000005', self.today)  # séance du jour

        self.assertEqual(
            self.backlog_numbers(self.today), ['CMAU0000001', 'CMAU0000002', 'CMAU0000003']
        )
        # En préparant la liste de demain, la séance du jour est aussi proposée.
        self.assertIn('CMAU0000005', self.backlog_numbers(self.tomorrow))

    def test_one_click_button_appears_once_the_list_is_started(self):
        old = self.make('CMAU0000001', self.yesterday, Container.Status.UNAVAILABLE, line=self.other)
        self.login('soir')
        url = reverse('terminal:carryover') + f'?date={self.tomorrow.isoformat()}'
        page = self.client.get(url)
        self.assertContains(page, 'CMAU0000001')
        self.assertContains(page, 'Aucune liste en préparation')
        self.assertNotContains(page, 'Ajouter à la liste du')

        self.make('CMAU0000009', self.tomorrow, Container.Status.PENDING)
        page = self.client.get(url)
        self.assertContains(page, 'Ajouter à la liste du')

        self.client.post(reverse('terminal:carryover_decide'), {
            'decision': 'carry', 'ids': str(old.pk), 'date': self.tomorrow.isoformat(),
        })
        new = Container.objects.get(container_number='CMAU0000001', scheduled_date=self.tomorrow)
        self.assertEqual(new.status, Container.Status.PENDING)
        self.assertEqual(new.shipping_line, self.other)
        self.assertEqual(new.carried_from, old)
        self.assertEqual(new.first_scheduled_date, self.yesterday)
        old.refresh_from_db()
        self.assertEqual(old.carry_decision, 'carried')
        self.assertNotIn('CMAU0000001', self.backlog_numbers(self.tomorrow))

        # Un second clic ne crée pas de doublon.
        self.client.post(reverse('terminal:carryover_decide'), {
            'decision': 'carry', 'ids': str(old.pk), 'date': self.tomorrow.isoformat(),
        })
        self.assertEqual(Container.objects.filter(container_number='CMAU0000001').count(), 2)

    def test_reject_removes_from_backlog_without_creating_anything(self):
        old = self.make('CMAU0000001', self.yesterday)
        self.login('adm')
        self.client.post(reverse('terminal:carryover_decide'), {
            'decision': 'reject', 'ids': str(old.pk), 'date': self.today.isoformat(),
        })
        old.refresh_from_db()
        self.assertEqual(old.carry_decision, 'rejected')
        self.assertEqual(Container.objects.count(), 1)
        self.assertEqual(self.backlog_numbers(self.today), [])

    def test_retyping_an_unverified_number_carries_it_instead_of_registering_again(self):
        old = self.make('CMAU0000001', self.yesterday, line=self.other)
        old_bulk = self.make('CMAU0000002', self.yesterday, line=self.other)
        self.login('soir')
        resp = self.client.post(reverse('terminal:container_add_single'), {
            'container_number': 'CMAU0000001',
            'shipping_line': str(self.cma.id),
            'status': 'pending',
            'scheduled_date': self.tomorrow.isoformat(),
        }, follow=True)
        self.assertContains(resp, 'déjà programmé depuis le')
        new = Container.objects.get(container_number='CMAU0000001', scheduled_date=self.tomorrow)
        self.assertEqual(new.carried_from, old)
        self.assertEqual(new.shipping_line, self.other)  # ligne d'origine conservée
        self.assertEqual(new.first_scheduled_date, self.yesterday)

        self.client.post(reverse('terminal:container_add_bulk'), {
            'container_list': 'CMAU0000002\nCMAU0000003',
            'shipping_line': str(self.cma.id),
            'default_status': 'pending',
            'scheduled_date': self.tomorrow.isoformat(),
        })
        carried = Container.objects.get(container_number='CMAU0000002', scheduled_date=self.tomorrow)
        fresh = Container.objects.get(container_number='CMAU0000003', scheduled_date=self.tomorrow)
        self.assertEqual(carried.carried_from, old_bulk)
        self.assertEqual(carried.shipping_line, self.other)
        self.assertIsNone(fresh.carried_from)
        self.assertEqual(fresh.first_scheduled_date, self.tomorrow)
        self.assertEqual(self.backlog_numbers(self.tomorrow), [])

    def test_morning_supervisor_can_only_carry_into_today(self):
        old = self.make('CMAU0000001', self.yesterday)
        self.login('matin')
        self.client.post(reverse('terminal:carryover_decide'), {
            'decision': 'carry', 'ids': str(old.pk), 'date': self.tomorrow.isoformat(),
        })
        self.assertTrue(
            Container.objects.filter(container_number='CMAU0000001', scheduled_date=self.today).exists()
        )
        self.assertFalse(
            Container.objects.filter(container_number='CMAU0000001', scheduled_date=self.tomorrow).exists()
        )

    def test_statistics_totals_and_rate(self):
        a = self.make('CMAU0000001', self.today)
        b = self.make('CMAU0000002', self.today)
        self.make('CMAU0000003', self.today)
        self.make('CMAU0000004', self.today, Container.Status.PENDING)
        a.set_verification('verified', acting_user=self.admin)
        b.set_verification('not_verified', acting_user=self.admin)

        self.login('matin')
        resp = self.client.get(reverse('terminal:statistics'), {'periode': 'semaine'})
        self.assertEqual(resp.status_code, 200)
        totals = resp.context['totals']
        self.assertEqual(totals['scheduled'], 4)
        self.assertEqual(totals['confirmed'], 3)
        self.assertEqual(totals['verified'], 1)
        self.assertEqual(totals['not_verified'], 1)
        self.assertEqual(totals['to_verify'], 1)
        self.assertEqual(totals['rate'], 33)
        self.assertEqual(len(resp.context['buckets']), 7)

        month = self.client.get(reverse('terminal:statistics'), {'periode': 'mois'})
        self.assertEqual(month.context['totals']['verified'], 1)
        csv_resp = self.client.get(reverse('terminal:statistics'), {'periode': 'semaine', 'export': 'csv'})
        self.assertEqual(csv_resp['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('TOTAL;4;3;1;1;1', csv_resp.content.decode('utf-8'))

        self.login('cli')
        self.assertEqual(self.client.get(reverse('terminal:statistics')).status_code, 302)

    def test_portal_pages_render_with_sidebar_for_each_staff_role(self):
        self.make('CMAU0000001', self.yesterday)
        self.make('CMAU0000002', self.today)
        for username in ('adm', 'soir', 'matin'):
            self.login(username)
            for name in ('dashboard', 'carryover', 'statistics', 'status_logs'):
                resp = self.client.get(reverse(f'terminal:{name}'))
                self.assertEqual(resp.status_code, 200, f'{username} {name}')
                self.assertContains(resp, 'portal-nav')
                self.assertContains(resp, 'Non vérifiés')
        self.login('adm')
        for name in ('terminal:shipping_lines', 'terminal:prefixes', 'accounts:user_list'):
            self.assertContains(self.client.get(reverse(name)), 'portal-nav')
        dash = self.client.get(reverse('terminal:dashboard'))
        self.assertContains(dash, 'Ajouter à cette liste')
        self.assertContains(dash, 'Vérification de la séance')

    def test_public_page_shows_verified(self):
        c = self.make('CMAU0000001', self.today)
        c.set_verification('verified', acting_user=self.admin)
        resp = self.client.get(reverse('terminal:verification'), {'q': 'CMAU0000001'})
        self.assertContains(resp, 'status-verified')
        self.assertContains(resp, 'Vérification douanière effectuée')
        self.assertNotContains(resp, 'Déplacement autorisé')
