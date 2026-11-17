from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from terminal.models import PortalAuditLog


class Command(BaseCommand):
    help = "Promeut un compte utilisateur existant en Superadmin (is_superuser=True) et journalise l'opération."

    def add_arguments(self, parser):
        parser.add_argument('username', help="Nom d'utilisateur (username) du compte à promouvoir.")

    def handle(self, *args, **options):
        username = options['username'].strip()
        user = User.objects.filter(username=username).first()
        if user is None:
            raise CommandError(f"Utilisateur « {username} » introuvable.")

        if user.is_superuser and user.is_staff:
            self.stdout.write(f"L'utilisateur « {username} » est déjà Superadmin.")
            return

        old_desc = f"role={user.role}, is_superuser={user.is_superuser}, is_staff={user.is_staff}"
        user.is_superuser = True
        user.is_staff = True
        if user.role not in (User.Role.ADMIN,):
            user.role = User.Role.ADMIN
        user.save(update_fields=['is_superuser', 'is_staff', 'role'])

        new_desc = f"role={user.role}, is_superuser={user.is_superuser}, is_staff={user.is_staff}"
        PortalAuditLog.objects.create(
            category=PortalAuditLog.Category.PROMOTE_SUPERADMIN,
            target_label=f"User:{user.username}",
            old_value=old_desc,
            new_value=new_desc,
            reason="Promotion manuelle via promote_superadmin",
            changed_by=None,
        )
        self.stdout.write(self.style.SUCCESS(f"Compte « {username} » promu Superadmin et journalisé."))
