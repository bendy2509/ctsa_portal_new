import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from terminal.models import Container, ContainerStatusLog, Motif, ShippingLine
from terminal.views import PAGE_SIZE, unverified_backlog


class VerificationTrackingTests(TestCase):
    """Vérification de séance, suivi des non vérifiés, reprise en un clic et statistiques."""

    def setUp(self):
        self.today = timezone.localdate()
        self.yesterday = self.today - datetime.timedelta(days=1)
        self.tomorrow = self.today + datetime.timedelta(days=1)
        self.cma = ShippingLine.objects.create(name='CMA CGM', code='CMA')
        self.other = ShippingLine.objects.create(name='Antillean Marine', code='ANT')
        self.not_verified_motif, _ = Motif.objects.get_or_create(
            type=Motif.Type.NOT_VERIFIED,
            label='Douane absente',
            defaults={'requires_note': False, 'is_active': True},
        )
        self.admin = User.objects.create_user(username='adm', password='Password123!', role=User.Role.ADMIN)
        self.supervisor = User.objects.create_user(
            username='sup', password='Password123!', role=User.Role.SUPERVISOR
        )
        self.customer = User.objects.create_user(username='cli', password='Password123!', role=User.Role.CLIENT)

    def make(self, number, date, status=Container.Status.CONFIRMED, line=None):
        return Container.objects.create(
            container_number=number, scheduled_date=date, status=status, shipping_line=line or self.cma
        )

    def login(self, username):
        self.client.logout()
        self.client.login(username=username, password='Password123!')

    def mark(self, container, result, **extra):
        payload = {'result': result, **extra}
        if result == 'not_verified' and 'verification_reason' not in payload:
            payload['verification_reason'] = str(self.not_verified_motif.pk)
        return self.client.post(
            reverse('terminal:container_set_verification', args=[container.pk]), payload
        )

    def backlog_numbers(self, target):
        return sorted(unverified_backlog(target).values_list('container_number', flat=True))

    def test_three_staff_roles_can_mark_but_client_cannot(self):
        for idx, username in enumerate(['adm', 'sup']):
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
        self.login('sup')
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
        self.login('sup')
        url = reverse('terminal:carryover') + f'?date={self.tomorrow.isoformat()}'
        page = self.client.get(url)
        self.assertContains(page, 'CMAU0000001')
        self.assertContains(page, 'Séance cible non démarrée')

        self.make('CMAU0000009', self.tomorrow, Container.Status.PENDING)
        page = self.client.get(url)
        self.assertContains(page, 'Séance cible active')

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
        self.login('sup')
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

    def test_supervisor_can_carry_into_tomorrow(self):
        old = self.make('CMAU0000001', self.yesterday)
        self.login('sup')
        self.client.post(reverse('terminal:carryover_decide'), {
            'decision': 'carry', 'ids': str(old.pk), 'date': self.tomorrow.isoformat(),
        })
        self.assertTrue(
            Container.objects.filter(container_number='CMAU0000001', scheduled_date=self.tomorrow).exists()
        )

    def test_statistics_totals_and_rate(self):
        a = self.make('CMAU0000001', self.today)
        b = self.make('CMAU0000002', self.today)
        self.make('CMAU0000003', self.today)
        self.make('CMAU0000004', self.today, Container.Status.PENDING)
        a.set_verification('verified', acting_user=self.admin)
        b.set_verification('not_verified', acting_user=self.admin)

        self.login('adm')
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
        self.assertIn('TOTAL;4;3;1;1;0;0;33', csv_resp.content.decode('utf-8'))

        self.login('cli')
        self.assertEqual(self.client.get(reverse('terminal:statistics')).status_code, 403)

    def test_portal_pages_render_with_sidebar_for_each_staff_role(self):
        self.make('CMAU0000001', self.yesterday)
        self.make('CMAU0000002', self.today)
        role_routes = {
            'adm': ('dashboard', 'containers', 'prepare', 'inspection', 'carryover', 'statistics', 'register', 'status_logs'),
            'sup': ('dashboard', 'containers', 'prepare', 'inspection', 'carryover'),
        }
        for username, routes in role_routes.items():
            self.login(username)
            for name in routes:
                resp = self.client.get(reverse(f'terminal:{name}'))
                self.assertEqual(resp.status_code, 200, f'{username} {name}')
                self.assertContains(resp, 'portal-nav')
                self.assertContains(resp, reverse('terminal:inspection'))
        self.login('adm')
        for name in ('inspection', 'carryover'):
            self.assertContains(self.client.get(reverse(f'terminal:{name}')), reverse('terminal:carryover'))
        for name in ('terminal:shipping_lines', 'accounts:user_list'):
            self.assertContains(self.client.get(reverse(name)), 'portal-nav')
        dash = self.client.get(reverse('terminal:dashboard'))
        self.assertContains(dash, reverse('terminal:inspection'))
        # La reprise des non vérifiés renvoie vers l'écran dédié « Non vérifiés ».
        self.assertNotContains(dash, 'Ajouter à cette liste')
        self.assertContains(self.client.get(reverse('terminal:prepare')), reverse('terminal:carryover'))
        # Le tableau de bord ne porte plus les boutons de marquage : ils sont dans Inspection.
        self.assertNotContains(dash, 'name="result" value="verified"')

    def test_not_verified_note_is_saved_then_cleared_when_marking_changes(self):
        self.login('sup')
        c = self.make('CMAU0000001', self.today)
        self.client.post(
            reverse('terminal:container_set_verification', args=[c.pk]),
            {
                'result': 'not_verified',
                'verification_reason': str(self.not_verified_motif.pk),
                'verification_note': 'Douane absente',
            },
        )
        c.refresh_from_db()
        self.assertEqual(c.verification_note, 'Douane absente')
        self.assertContains(self.client.get(reverse('terminal:inspection')), 'Douane absente')
        self.mark(c, 'verified')
        c.refresh_from_db()
        self.assertEqual(c.verification_result, 'verified')
        self.assertEqual(c.verification_note, '')

    def test_deleting_a_carried_container_puts_the_original_back_in_the_backlog(self):
        old = self.make('CMAU0000001', self.yesterday)
        self.login('adm')
        self.client.post(reverse('terminal:carryover_decide'), {
            'decision': 'carry', 'ids': str(old.pk), 'date': self.today.isoformat(),
        })
        self.assertEqual(self.backlog_numbers(self.today), [])
        new = Container.objects.get(container_number='CMAU0000001', scheduled_date=self.today)
        self.client.post(reverse('terminal:container_delete', args=[new.pk]))
        self.assertEqual(self.backlog_numbers(self.today), ['CMAU0000001'])

    def test_destructive_buttons_carry_their_own_confirmation_text(self):
        self.make('CMAU0000001', self.yesterday)
        c_today = self.make('CMAU0000002', self.today)
        self.login('adm')
        detail = self.client.get(reverse('terminal:container_detail', args=[c_today.pk]))
        self.assertContains(detail, 'id="ui-confirm-dialog"')
        self.assertContains(detail, f'data-confirm-title="Supprimer {c_today.container_number} ?"')
        page = self.client.get(reverse('terminal:carryover'))
        self.assertContains(page, 'data-confirm-title="Rejeter ce conteneur ?"')
        self.assertContains(page, 'data-confirm-title="Rejeter la sélection ?"')

    def test_state_signature_changes_with_status_and_reveals_nothing(self):
        url = reverse('terminal:verification_state')
        unknown = self.client.get(url, {'q': 'ZZZU9999999'}).json()['signature']
        self.assertEqual(len(unknown), 20)
        self.assertEqual(self.client.get(url, {'q': 'pas-un-numero'}).json()['signature'], '')

        c = self.make('CMAU0000001', self.today, Container.Status.PENDING)
        first = self.client.get(url, {'q': 'cmau-0000001'}).json()['signature']
        self.assertEqual(len(first), 20)
        self.assertNotIn('CMAU', first)
        self.assertEqual(first, self.client.get(url, {'q': 'CMAU0000001'}).json()['signature'])

        page = self.client.get(reverse('terminal:verification'), {'q': 'CMAU0000001'})
        self.assertContains(page, f'data-refresh-signature="{first}"')

        c.status = Container.Status.CONFIRMED
        c.save(acting_user=self.admin)
        second = self.client.get(url, {'q': 'CMAU0000001'}).json()['signature']
        self.assertNotEqual(first, second)

        # Une fois vérifié, la fiche ne change plus : plus d'actualisation automatique.
        c.set_verification('verified', acting_user=self.admin)
        page = self.client.get(reverse('terminal:verification'), {'q': 'CMAU0000001'})
        self.assertNotContains(page, 'data-refresh-url')

    def test_watchlist_state_is_private_and_follows_saved_containers(self):
        url = reverse('terminal:client_watchlist_state')
        self.assertEqual(self.client.get(url).status_code, 302)
        self.login('cli')
        self.client.post(reverse('terminal:client_watchlist'), {'container_number': 'CMAU0000001'})
        before = self.client.get(url).json()['signature']
        self.assertContains(
            self.client.get(reverse('terminal:client_watchlist')), f'data-refresh-signature="{before}"'
        )
        self.make('CMAU0000001', self.today)
        self.assertNotEqual(before, self.client.get(url).json()['signature'])

    def test_history_and_backlog_are_paginated_and_keep_filters(self):
        for i in range(65):
            self.make(f'CMAU{i:07d}', self.yesterday, Container.Status.PENDING)
        self.login('adm')

        first = self.client.get(reverse('terminal:carryover'), {'status': 'pending'})
        self.assertEqual(len(first.context['rows']), PAGE_SIZE)
        self.assertContains(first, f'Lignes 1 à {PAGE_SIZE} sur 65')
        self.assertContains(first, 'status=pending&amp;page=2')
        second = self.client.get(reverse('terminal:carryover'), {'status': 'pending', 'page': 2})
        self.assertEqual(len(second.context['rows']), PAGE_SIZE)
        self.assertEqual(second.context['breakdown']['total'], 65)

        logs = self.client.get(reverse('terminal:status_logs'), {'q': 'CMAU'})
        self.assertEqual(len(logs.context['logs']), PAGE_SIZE)
        self.assertContains(logs, f'Lignes 1 à {PAGE_SIZE} sur 65')
        self.assertContains(logs, 'q=CMAU&amp;page=2')

    def test_close_backlog_command_simulation_then_real_run(self):
        from io import StringIO
        from django.core.management import call_command

        old = self.make('CMAU0000001', self.yesterday)
        verified = self.make('CMAU0000002', self.yesterday)
        verified.set_verification('verified', acting_user=self.admin)
        current = self.make('CMAU0000003', self.today)

        out = StringIO()
        call_command('clore_non_verifies', avant=self.today.isoformat(), simulation=True, stdout=out)
        self.assertIn('Simulation : 1 conteneur', out.getvalue())
        old.refresh_from_db()
        self.assertEqual(old.carry_decision, '')

        call_command('clore_non_verifies', avant=self.today.isoformat(), stdout=StringIO())
        for c in (old, verified, current):
            c.refresh_from_db()
        self.assertEqual(old.carry_decision, 'rejected')
        self.assertEqual(verified.carry_decision, '')
        self.assertEqual(current.carry_decision, '')
        self.assertTrue(ContainerStatusLog.objects.filter(container=old, action='rejected').exists())

    def test_dashboard_shows_only_the_list_and_prepare_page_holds_the_forms(self):
        self.make('CMAU0000001', self.yesterday)
        pending = self.make('CMAU0000002', self.today, Container.Status.PENDING)
        self.login('sup')

        dash = self.client.get(reverse('terminal:containers'), {'date': self.today.isoformat()})
        # Plus de formulaire d'inscription ni de formulaire incrusté dans le tableau.
        self.assertNotContains(dash, 'name="container_list"')
        self.assertNotContains(dash, 'id="add-single-number"')
        self.assertContains(dash, reverse('terminal:container_detail', args=[pending.pk]))
        self.assertContains(dash, 'data-selection-bar="dashboard" hidden')

    def test_prepare_page_registers_and_comes_back_to_itself(self):
        self.make('CMAU0000001', self.yesterday)
        self.login('sup')
        url = reverse('terminal:prepare') + f'?date={self.tomorrow.isoformat()}'
        page = self.client.get(url)
        self.assertEqual(page.context['selected_date'], self.tomorrow)
        self.assertEqual(page.context['registered_count'], 0)
        self.assertContains(page, 'id="add-single-number"')
        self.assertContains(page, 'name="container_list"')

        resp = self.client.post(reverse('terminal:container_add_single'), {
            'container_number': 'CMAU0000005', 'shipping_line': str(self.cma.id), 'status': 'pending',
            'scheduled_date': self.tomorrow.isoformat(), 'next': url,
        })
        self.assertRedirects(resp, url, fetch_redirect_response=False)
        after = self.client.get(url)
        self.assertEqual(after.context['registered_count'], 1)
        self.assertEqual([c.container_number for c in after.context['recent']], ['CMAU0000005'])

        # Une date passée est ramenée à aujourd'hui ; le superviseur peut préparer aujourd'hui ou demain.
        past = self.client.get(reverse('terminal:prepare'), {'date': self.yesterday.isoformat()})
        self.assertEqual(past.context['selected_date'], self.today)
        tomorrow_page = self.client.get(reverse('terminal:prepare'), {'date': self.tomorrow.isoformat()})
        self.assertEqual(tomorrow_page.context['selected_date'], self.tomorrow)

    def test_inspection_lists_confirmed_containers_of_a_started_session(self):
        confirmed = self.make('CMAU0000001', self.today)
        self.make('CMAU0000002', self.today, Container.Status.PENDING)
        old = self.make('CMAU0000003', self.yesterday)
        self.login('sup')

        page = self.client.get(reverse('terminal:inspection'))
        self.assertEqual([c.container_number for c in page.context['rows']], ['CMAU0000001'])
        self.assertEqual(page.context['stats']['to_verify'], 1)
        self.assertEqual(page.context['stats']['not_confirmed'], 1)
        self.assertContains(page, 'name="result" value="verified"')

        # Une date future est ramenée à aujourd'hui : on n'inspecte pas une séance à venir.
        future = self.client.get(reverse('terminal:inspection'), {'date': self.tomorrow.isoformat()})
        self.assertEqual(future.context['selected_date'], self.today)

        # Marquage groupé de la sélection, sur une séance passée, avec retour sur la page.
        back = reverse('terminal:inspection') + f'?date={self.yesterday.isoformat()}'
        resp = self.client.post(reverse('terminal:container_verify_bulk'), {
            'date': self.yesterday.isoformat(), 'ids': [str(old.pk)], 'result': 'verified', 'next': back,
        })
        self.assertRedirects(resp, back, fetch_redirect_response=False)
        old.refresh_from_db()
        confirmed.refresh_from_db()
        self.assertEqual(old.verification_result, 'verified')
        self.assertEqual(confirmed.verification_result, '')

    def test_register_finds_when_a_container_was_verified(self):
        import datetime
        long_ago = self.today - datetime.timedelta(days=90)
        first = self.make('CMAU0000001', long_ago, Container.Status.UNAVAILABLE)
        second = self.make('CMAU0000001', self.yesterday)
        second.set_verification('verified', acting_user=self.admin)
        self.make('CMAU0000002', self.today)
        self.login('adm')

        # Sans dates, la recherche d'un numéro couvre tout l'historique.
        found = self.client.get(reverse('terminal:register'), {'q': 'cmau 0000001'})
        self.assertEqual({c.pk for c in found.context['rows']}, {first.pk, second.pk})
        self.assertIsNone(found.context['start'])
        self.assertContains(found, "tout l'historique")
        self.assertContains(found, self.yesterday.strftime('%d/%m/%Y'))
        self.assertEqual(found.context['totals']['verified'], 1)

        # Par défaut : les 30 derniers jours, avec les quantités de l'intervalle.
        recent = self.client.get(reverse('terminal:register'))
        self.assertEqual(recent.context['totals']['scheduled'], 2)
        self.assertEqual(recent.context['totals']['confirmed'], 2)
        self.assertEqual(recent.context['totals']['verified'], 1)
        self.assertEqual(recent.context['totals']['to_verify'], 1)

        only_verified = self.client.get(reverse('terminal:register'), {'resultat': 'verified'})
        self.assertEqual([c.pk for c in only_verified.context['rows']], [second.pk])
        self.assertEqual(only_verified.context['totals']['scheduled'], 2)

        csv_resp = self.client.get(reverse('terminal:register'), {'q': 'CMAU0000001', 'export': 'csv'})
        body = csv_resp.content.decode('utf-8')
        self.assertIn('CMAU0000001', body)
        self.assertIn('Vérifié', body)

        # Le registre est en lecture seule ; le marquage Vérifié/Non vérifié se fait sur « Inspection ».
        self.make('CMAU0000009', self.today)
        self.assertContains(self.client.get(reverse('terminal:inspection')), 'name="result" value="verified"')

    def test_public_page_shows_verified(self):
        c = self.make('CMAU0000001', self.today)
        c.set_verification('verified', acting_user=self.admin)
        resp = self.client.get('/fr/verification/', {'q': 'CMAU0000001'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['result'].is_verified)
        self.assertContains(resp, 'Déjà vérifié')
        self.assertContains(resp, 'Conteneur déjà vérifié')
        self.assertNotContains(resp, 'Déplacement autorisé')

    def test_all_new_additions_default_to_pending_even_if_previously_confirmed(self):
        # Conteneur confirmé (et même vérifié) dans la séance précédente
        prev = self.make('CMAU0000010', self.yesterday, Container.Status.CONFIRMED)
        prev.set_verification('verified', acting_user=self.admin)
        unverified_prev = self.make('CMAU0000011', self.yesterday, Container.Status.CONFIRMED)

        self.login('sup')
        # Ajout unitaire en tentant d'envoyer status='confirmed' (avec confirmation de réinscription)
        self.client.post(reverse('terminal:container_add_single'), {
            'container_number': 'CMAU0000010',
            'shipping_line': str(self.cma.id),
            'status': Container.Status.CONFIRMED,
            'scheduled_date': self.tomorrow.isoformat(),
            'confirm_already_verified': '1',
        })
        new_single = Container.objects.get(container_number='CMAU0000010', scheduled_date=self.tomorrow)
        self.assertEqual(new_single.status, Container.Status.PENDING)

        # Ajout en lot en tentant d'envoyer default_status='confirmed' (reprise + nouveau)
        self.client.post(reverse('terminal:container_add_bulk'), {
            'container_list': 'CMAU0000011\nCMAU0000012',
            'shipping_line': str(self.cma.id),
            'default_status': Container.Status.CONFIRMED,
            'scheduled_date': self.tomorrow.isoformat(),
        })
        carried = Container.objects.get(container_number='CMAU0000011', scheduled_date=self.tomorrow)
        fresh = Container.objects.get(container_number='CMAU0000012', scheduled_date=self.tomorrow)
        self.assertEqual(carried.carried_from, unverified_prev)
        self.assertEqual(carried.status, Container.Status.PENDING)
        self.assertEqual(fresh.status, Container.Status.PENDING)

        # La liste du tableau de bord affiche bien le sélecteur rapide 1 clic (Confirmé / En attente / Indisponible)
        dash = self.client.get(reverse('terminal:containers'), {'date': self.tomorrow.isoformat()})
        self.assertContains(dash, f'data-toggle-unavailable="{fresh.pk}"')
        self.assertContains(dash, 'status-segmented')

    def test_dashboard_and_inspection_are_paginated(self):
        for i in range(65):
            self.make(f'CMAU{i:07d}', self.today, Container.Status.CONFIRMED)
        self.login('adm')
        expected_pages = (65 + PAGE_SIZE - 1) // PAGE_SIZE

        dash_p1 = self.client.get(reverse('terminal:containers'), {'date': self.today.isoformat()})
        self.assertEqual(len(dash_p1.context['containers']), PAGE_SIZE)
        self.assertContains(dash_p1, f'Page 1 / {expected_pages}')
        dash_p2 = self.client.get(reverse('terminal:containers'), {'date': self.today.isoformat(), 'page': 2})
        self.assertEqual(len(dash_p2.context['containers']), PAGE_SIZE)

        insp_p1 = self.client.get(reverse('terminal:inspection'), {'date': self.today.isoformat()})
        self.assertEqual(len(insp_p1.context['rows']), PAGE_SIZE)
        self.assertContains(insp_p1, f'Page 1 / {expected_pages}')
        insp_p2 = self.client.get(reverse('terminal:inspection'), {'date': self.today.isoformat(), 'page': 2})
        self.assertEqual(len(insp_p2.context['rows']), PAGE_SIZE)

    def test_dashboard_summarises_the_day_and_links_to_the_list(self):
        self.make('CMAU0000001', self.today, Container.Status.CONFIRMED)
        self.make('CMAU0000002', self.today, Container.Status.PENDING)
        self.make('CMAU0000003', self.yesterday, Container.Status.PENDING)
        self.login('adm')

        resp = self.client.get(reverse('terminal:dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['stats']['total'], 2)
        self.assertEqual(resp.context['stats']['pending'], 1)
        self.assertEqual(len(resp.context['week']), 7)
        self.assertEqual(resp.context['by_line'][0]['total'], 2)
        self.assertEqual(resp.context['backlog']['total'], 1)
        # Le tableau de bord ne contient pas la liste : il renvoie vers elle, filtrée.
        self.assertNotContains(resp, 'ctsa-table')
        self.assertContains(resp, reverse('terminal:containers'))
        self.assertContains(resp, 'status=pending')

        self.client.logout()
        self.login('cli')
        self.assertEqual(self.client.get(reverse('terminal:dashboard')).status_code, 403)

    def test_single_add_allows_missing_shipping_line(self):
        self.login('sup')
        self.client.post(reverse('terminal:container_add_single'), {
            'container_number': 'CMAU0000020',
            'shipping_line': '',
            'scheduled_date': self.tomorrow.isoformat(),
        })
        c = Container.objects.get(container_number='CMAU0000020', scheduled_date=self.tomorrow)
        self.assertIsNone(c.shipping_line)
        self.assertEqual(c.status, Container.Status.PENDING)

    def test_bulk_add_allows_optional_shipping_line(self):
        self.login('sup')
        self.client.post(reverse('terminal:container_add_bulk'), {
            'container_list': 'CMAU0000020\nASJU0000021',
            'shipping_line': '',
            'scheduled_date': self.tomorrow.isoformat(),
        })
        c1 = Container.objects.get(container_number='CMAU0000020', scheduled_date=self.tomorrow)
        c2 = Container.objects.get(container_number='ASJU0000021', scheduled_date=self.tomorrow)
        self.assertIsNone(c1.shipping_line)
        self.assertIsNone(c2.shipping_line)
        self.assertEqual(c1.status, Container.Status.PENDING)
        self.assertEqual(c2.status, Container.Status.PENDING)
