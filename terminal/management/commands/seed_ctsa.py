import datetime
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from accounts.models import User
from terminal.models import (
    ShippingLine,
    ContainerPrefix,
    UnavailabilityReason,
    Container,
    SavedContainer,
)


class Command(BaseCommand):
    help = "Initialise les données de démonstration pour Cap Terminal S.A. (CTSA Portal)"

    def handle(self, *args, **options):
        # Données de démonstration : jamais sur un portail en ligne.
        if not settings.DEBUG:
            raise CommandError(
                "seed_ctsa crée des comptes de démonstration : la commande est refusée hors mode développement."
            )
        # Le mot de passe des comptes de démonstration n'est plus écrit dans le dépôt.
        password = os.environ.get('CTSA_SEED_PASSWORD', '')
        if len(password) < 8:
            raise CommandError(
                "Définissez CTSA_SEED_PASSWORD (8 caractères minimum) avant de lancer seed_ctsa : "
                "c'est le mot de passe des comptes de démonstration."
            )

        # 1. Comptes utilisateurs par rôle
        admin_user, created = User.objects.get_or_create(
            username='admin',
            defaults={
                'email': 'admin@capterminal.com',
                'full_name': 'Jean-Marc Pierre (Admin CTSA)',
                'role': User.Role.ADMIN,
                'company_name': 'Cap Terminal S.A.',
                'phone': '+509 3701-1000',
                'is_staff': True,
                'is_superuser': True,
            },
        )
        if created:
            admin_user.set_password(password)
            admin_user.save()

        evening_sup, created = User.objects.get_or_create(
            username='superviseur.soir',
            defaults={
                'email': 'soir@capterminal.com',
                'full_name': 'Marc-Antoine Joseph (Superviseur Soir)',
                'role': User.Role.EVENING_SUPERVISOR,
                'company_name': 'Cap Terminal S.A. — Opérations RAMPE',
                'phone': '+509 3702-2000',
            },
        )
        if created:
            evening_sup.set_password(password)
            evening_sup.save()

        morning_sup, created = User.objects.get_or_create(
            username='superviseur.matin',
            defaults={
                'email': 'matin@capterminal.com',
                'full_name': 'Nadine Toussaint (Superviseur Matin)',
                'role': User.Role.MORNING_SUPERVISOR,
                'company_name': 'Cap Terminal S.A. — Opérations RAMPE',
                'phone': '+509 3703-3000',
            },
        )
        if created:
            morning_sup.set_password(password)
            morning_sup.save()

        client_user, created = User.objects.get_or_create(
            username='client.import',
            defaults={
                'email': 'transit@capimport.ht',
                'full_name': 'Patrick Bélizaire',
                'role': User.Role.CLIENT,
                'company_name': 'Nord Transit & Douane S.A.',
                'phone': '+509 3811-4500',
            },
        )
        if created:
            client_user.set_password(password)
            client_user.save()

        # 2. Lignes maritimes (capterminal.com + résumé métier)
        lines_data = [
            {
                'code': 'CMA',
                'name': 'CMA CGM',
                'contact_email': 'caphaitien@cma-cgm.com',
                'contact_phone': '+509 2260-1200',
                'address': 'Boulevard du Front de Mer, Cap-Haïtien',
                'website': 'https://www.cma-cgm.com',
            },
            {
                'code': 'CHL',
                'name': 'Challenger',
                'contact_email': 'ops@challenger-shipping.com',
                'contact_phone': '+509 2262-3400',
                'address': 'Port du Cap-Haïtien, Département du Nord',
                'website': '',
            },
            {
                'code': 'ACC',
                'name': 'Accordia Shipping',
                'contact_email': 'agency@accordiashipping.com',
                'contact_phone': '305-635-0938',
                'address': 'Zone Portuaire, Cap-Haïtien',
                'website': '',
            },
            {
                'code': 'ANT',
                'name': 'Antillean Marine',
                'contact_email': 'info@antillean.com',
                'contact_phone': '305-633-6361',
                'address': 'Miami River / Terminal Cap-Haïtien',
                'website': 'http://www.antillean.com',
            },
            {
                'code': 'SCL',
                'name': 'SC Line',
                'contact_email': 'roro@scline.com',
                'contact_phone': '305-365-0938',
                'address': 'Terminal RORO & Heavy Cargo, Cap-Haïtien',
                'website': 'http://www.scline.com',
            },
            {
                'code': 'ZIM',
                'name': 'ZIM Integrated Shipping',
                'contact_email': 'haiti.service@zim.com',
                'contact_phone': '+509 2813-0800',
                'address': 'Port du Cap-Haïtien',
                'website': 'https://www.zim.com',
            },
        ]

        lines_by_code = {}
        for item in lines_data:
            line, _ = ShippingLine.objects.update_or_create(
                code=item['code'],
                defaults=item,
            )
            lines_by_code[item['code']] = line

        # 3. Préfixes à 4 lettres
        prefixes_data = [
            ('CMAU', 'CMA', 'Conteneurs standards CMA CGM'),
            ('ECMU', 'CMA', 'Flotte secondaire CMA CGM'),
            ('ASJU', 'CHL', 'Conteneurs Challenger'),
            ('CHLU', 'CHL', 'Unités Challenger Maritime'),
            ('ACCU', 'ACC', 'Conteneurs Accordia'),
            ('ANTU', 'ANT', 'Antillean Marine Shipping Corp.'),
            ('SCLU', 'SCL', 'SC Line Heavy & Project Cargo'),
            ('ZIMU', 'ZIM', 'Conteneurs ZIM'),
        ]
        for prefix_code, line_code, desc in prefixes_data:
            ContainerPrefix.objects.update_or_create(
                prefix=prefix_code,
                defaults={
                    'shipping_line': lines_by_code[line_code],
                    'description': desc,
                },
            )

        # 4. Motifs d'indisponibilité prédéfinis
        reasons_data = [
            ('panne-reach-stacker', 'Panne reach stacker', 'Équipement de manutention momentanément indisponible.'),
            ('blocage-douane', 'Blocage administratif Douane (AGD)', 'Dossier en attente de validation douanière.'),
            ('fond-de-pile', 'Conteneur en fond de pile', 'Manutention supplémentaire requise au parc.'),
            ('absence-transitaire', 'Absence du client / commissionnaire', 'Représentant non présenté au créneau prévu.'),
            ('inspection-reportee', 'Vérification reportée au lendemain', 'Capacité maximale de la RAMPE atteinte.'),
        ]
        reasons_by_code = {}
        for code, label, desc in reasons_data:
            r, _ = UnavailabilityReason.objects.update_or_create(
                code=code,
                defaults={'label': label, 'description': desc, 'is_active': True},
            )
            reasons_by_code[code] = r

        # 5. Conteneurs programmés (aujourd'hui et demain)
        today = timezone.localdate()
        tomorrow = today + datetime.timedelta(days=1)

        sample_today = [
            ('CMAU4052450', lines_by_code['CMA'], Container.Status.CONFIRMED, None, '', evening_sup),
            ('ASJU2190841', lines_by_code['CHL'], Container.Status.CONFIRMED, None, '', evening_sup),
            ('ANTU7731092', lines_by_code['ANT'], Container.Status.CONFIRMED, None, '', evening_sup),
            ('CMAU1138108', lines_by_code['CMA'], Container.Status.PENDING, None, '', morning_sup),
            ('ZIMU5580124', lines_by_code['ZIM'], Container.Status.PENDING, None, '', morning_sup),
            (
                'SCLU9044117',
                lines_by_code['SCL'],
                Container.Status.UNAVAILABLE,
                reasons_by_code['panne-reach-stacker'],
                'Reach stacker n°2 en maintenance hydraulique',
                evening_sup,
            ),
            (
                'ACCU3309185',
                lines_by_code['ACC'],
                Container.Status.UNAVAILABLE,
                reasons_by_code['blocage-douane'],
                'Attente bordereau AGD',
                admin_user,
            ),
        ]

        for num, line_obj, status_val, reason_obj, note, author in sample_today:
            c, exists = Container.objects.get_or_create(
                container_number=num,
                scheduled_date=today,
                defaults={
                    'shipping_line': line_obj,
                    'status': Container.Status.PENDING,
                    'created_by': evening_sup,
                    'updated_by': evening_sup,
                },
            )
            c.shipping_line = line_obj
            c.status = status_val
            c.unavailability_reason = reason_obj
            c.unavailability_note = note
            c.save(acting_user=author)

        sample_tomorrow = [
            ('CMAU8821044', lines_by_code['CMA'], Container.Status.PENDING, None, '', evening_sup),
            ('ASJU6012930', lines_by_code['CHL'], Container.Status.PENDING, None, '', evening_sup),
            ('ANTU4409123', lines_by_code['ANT'], Container.Status.CONFIRMED, None, '', evening_sup),
        ]
        for num, line_obj, status_val, reason_obj, note, author in sample_tomorrow:
            c, _ = Container.objects.update_or_create(
                container_number=num,
                scheduled_date=tomorrow,
                defaults={
                    'shipping_line': line_obj,
                    'status': status_val,
                    'unavailability_reason': reason_obj,
                    'unavailability_note': note,
                    'created_by': author,
                    'updated_by': author,
                },
            )

        # 6. Conteneurs suivis par le client démo
        for num, lbl in [
            ('CMAU4052450', 'Lot Génératrices Cap-Haïtien'),
            ('CMAU1138108', 'Pièces détachées camions'),
            ('SCLU9044117', 'Matériel construction RORO'),
        ]:
            SavedContainer.objects.get_or_create(
                user=client_user,
                container_number=num,
                defaults={'label': lbl, 'notify_whatsapp': True},
            )

        self.stdout.write(self.style.SUCCESS("Données CTSA Portal initialisées avec succès."))
