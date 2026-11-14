import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from terminal.models import Container
from terminal.views import _close_tracking


class Command(BaseCommand):
    help = (
        "Sort du suivi des non vérifiés les conteneurs programmés avant une date : ils sont marqués "
        "« rejetés » et ne sont plus proposés pour les prochaines listes. À lancer une fois après la "
        "mise en place du suivi, pour ne pas voir tout l'historique apparaître comme « non vérifié »."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--avant',
            required=True,
            help="Date limite au format AAAA-MM-JJ : seules les séances strictement antérieures sont closes.",
        )
        parser.add_argument(
            '--simulation',
            action='store_true',
            help="Affiche ce qui serait clos, sans rien modifier.",
        )

    def handle(self, *args, **options):
        try:
            limit = datetime.date.fromisoformat(options['avant'])
        except ValueError as exc:
            raise CommandError("--avant attend une date au format AAAA-MM-JJ (ex. 2026-11-01).") from exc
        if limit > timezone.localdate():
            raise CommandError("--avant ne peut pas être dans le futur : les séances à venir ne sont pas closes.")

        backlog = (
            Container.objects.filter(scheduled_date__lt=limit, carry_decision='')
            .exclude(verification_result=Container.Verification.VERIFIED)
            .order_by('scheduled_date', 'container_number')
        )
        total = backlog.count()
        if not total:
            self.stdout.write("Aucun conteneur non vérifié avant cette date.")
            return

        first, last = backlog.first().scheduled_date, backlog.last().scheduled_date
        summary = (
            f"{total} conteneur(s) non vérifié(s), séances du {first.strftime('%d/%m/%Y')} "
            f"au {last.strftime('%d/%m/%Y')}"
        )
        if options['simulation']:
            self.stdout.write(f"Simulation : {summary} seraient clos. Rien n'a été modifié.")
            return

        reason = f"Clôture en lot des séances antérieures au {limit.strftime('%d/%m/%Y')}"
        for container in backlog.iterator():
            _close_tracking(container, Container.CarryDecision.REJECTED, None, reason)
        self.stdout.write(self.style.SUCCESS(f"{summary} : clos et tracés dans l'historique."))
