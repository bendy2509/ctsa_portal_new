import io
import os
import re
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from PIL import Image, ImageOps

# Create your models here.

CONTAINER_NUMBER_REGEX = re.compile(r'^[A-Z]{4}\d{7}$')


def normalize_container_number(value: str) -> str:
    if not value:
        return ''
    return re.sub(r'[\s\-]+', '', value).upper()


def validate_container_number(value: str):
    normalized = normalize_container_number(value)
    if not CONTAINER_NUMBER_REGEX.match(normalized):
        raise ValidationError(
            "Le numéro de conteneur doit comporter 4 lettres suivies de 7 chiffres (ex. CMAU4052450)."
        )


class ShippingLine(models.Model):
    name = models.CharField(max_length=120, unique=True, verbose_name="Nom de la ligne maritime")
    code = models.CharField(max_length=20, blank=True, default='', verbose_name="Code interne")
    contact_email = models.EmailField(blank=True, verbose_name="Courriel")
    contact_phone = models.CharField(max_length=40, blank=True, verbose_name="Téléphone")
    address = models.CharField(max_length=220, blank=True, verbose_name="Adresse / Représentation")
    website = models.URLField(blank=True, verbose_name="Site web")
    is_active = models.BooleanField(default=True, verbose_name="Active")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'shipping_lines'
        verbose_name = "Ligne maritime"
        verbose_name_plural = "Lignes maritimes"
        ordering = ['name']

    def save(self, *args, **kwargs):
        if not self.code and self.name:
            from django.utils.text import slugify
            self.code = slugify(self.name)[:20].upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ContainerPrefix(models.Model):
    prefix = models.CharField(
        max_length=4,
        unique=True,
        verbose_name="Préfixe (4 lettres)",
        help_text="Code propriétaire BIC à 4 lettres (ex. CMAU, ASJU, ANTU).",
    )
    shipping_line = models.ForeignKey(
        ShippingLine,
        on_delete=models.CASCADE,
        related_name='prefixes',
        verbose_name="Ligne maritime",
    )
    description = models.CharField(max_length=160, blank=True, verbose_name="Note / Description")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'container_prefixes'
        verbose_name = "Préfixe de conteneur"
        verbose_name_plural = "Préfixes de conteneurs"
        ordering = ['prefix']

    def clean(self):
        self.prefix = (self.prefix or '').strip().upper()
        if not re.match(r'^[A-Z]{4}$', self.prefix):
            raise ValidationError({'prefix': "Le préfixe doit contenir exactement 4 lettres (ex. CMAU)."})

    def save(self, *args, **kwargs):
        self.prefix = (self.prefix or '').strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.prefix} → {self.shipping_line.name}"


class Container(models.Model):
    class Status(models.TextChoices):
        CONFIRMED = 'confirmed', 'Confirmé'
        PENDING = 'pending', 'En attente'
        UNAVAILABLE = 'unavailable', 'Indisponible'

    class Verification(models.TextChoices):
        VERIFIED = 'verified', 'Vérifié'
        NOT_VERIFIED = 'not_verified', 'Non vérifié'

    class CarryDecision(models.TextChoices):
        CARRIED = 'carried', 'Repris'
        REJECTED = 'rejected', 'Rejeté'

    container_number = models.CharField(
        max_length=11,
        validators=[validate_container_number],
        db_index=True,
        verbose_name="Numéro de conteneur",
    )
    prefix_ref = models.ForeignKey(
        ContainerPrefix,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='containers',
        verbose_name="Préfixe détecté",
    )
    shipping_line = models.ForeignKey(
        ShippingLine,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='containers',
        verbose_name="Ligne maritime",
    )
    scheduled_date = models.DateField(
        default=timezone.localdate,
        db_index=True,
        verbose_name="Date de vérification (RAMPE)",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name="Statut",
    )
    unavailability_note = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Motif d'indisponibilité",
    )
    # --- Résultat de la séance de vérification douanière ---
    verification_result = models.CharField(
        max_length=16,
        choices=Verification.choices,
        blank=True,
        default='',
        db_index=True,
        verbose_name="Vérification",
    )
    verification_note = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Précision sur la vérification",
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_containers',
        verbose_name="Vérification marquée par",
    )
    verified_at = models.DateTimeField(null=True, blank=True, verbose_name="Vérification marquée le")

    # --- Suivi d'un conteneur programmé mais pas encore vérifié ---
    first_scheduled_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Première programmation",
        help_text="Date de la toute première inscription ; conservée à chaque reprise.",
    )
    carried_from = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='carried_to',
        verbose_name="Repris de la séance",
    )
    carry_decision = models.CharField(
        max_length=16,
        choices=CarryDecision.choices,
        blank=True,
        default='',
        db_index=True,
        verbose_name="Décision de report",
    )
    carry_decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='carry_decisions',
        verbose_name="Décision de report par",
    )
    carry_decided_at = models.DateTimeField(null=True, blank=True, verbose_name="Décision de report le")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_containers',
        verbose_name="Créé par",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='updated_containers',
        verbose_name="Dernière modification par",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Créé le")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Mis à jour le")

    class Meta:
        db_table = 'containers'
        verbose_name = "Conteneur programmé"
        verbose_name_plural = "Conteneurs programmés"
        ordering = ['-scheduled_date', 'container_number']
        constraints = [
            models.UniqueConstraint(
                fields=['container_number', 'scheduled_date'],
                name='unique_container_per_day',
            )
        ]

    def __str__(self):
        return f"{self.container_number} — {self.scheduled_date} ({self.get_status_display()})"

    @property
    def prefix_code(self) -> str:
        return (self.container_number or '')[:4].upper()

    @property
    def full_reason_display(self) -> str:
        if self.status != self.Status.UNAVAILABLE:
            return ''
        return self.unavailability_note or "Motif non précisé"

    @property
    def is_verified(self) -> bool:
        return self.verification_result == self.Verification.VERIFIED

    @property
    def can_be_verified(self) -> bool:
        """Seul un conteneur confirmé, dont la séance a commencé, peut être marqué."""
        return (
            self.status == self.Status.CONFIRMED
            and self.scheduled_date <= timezone.localdate()
        )

    @property
    def needs_carry_decision(self) -> bool:
        """Conteneur « à suivre » : programmé, séance passée, ni vérifié, ni repris, ni rejeté."""
        return (
            not self.is_verified
            and not self.carry_decision
            and self.scheduled_date < timezone.localdate()
        )

    @property
    def days_since_first_scheduled(self) -> int:
        start = self.first_scheduled_date or self.scheduled_date
        return max((timezone.localdate() - start).days, 0)

    def set_verification(self, result: str, acting_user=None, note: str = ''):
        """Marque le conteneur Vérifié / Non vérifié ('' pour annuler) et trace l'opération."""
        if result not in ('', *self.Verification.values):
            raise ValidationError("Résultat de vérification inconnu.")
        if result and not self.can_be_verified:
            raise ValidationError(
                "Seul un conteneur confirmé, dont la séance a commencé, peut être marqué vérifié ou non vérifié."
            )
        note = note if result == self.Verification.NOT_VERIFIED else ''
        if result == self.verification_result and note == self.verification_note:
            return
        user = acting_user if getattr(acting_user, 'is_authenticated', False) else None
        self.verification_result = result
        self.verification_note = note
        self.verified_by = user if result else None
        self.verified_at = timezone.now() if result else None
        self.save(acting_user=acting_user)
        ContainerStatusLog.objects.create(
            container=self,
            action=result or ContainerStatusLog.Action.VERIFICATION_CLEARED,
            old_status=self.status,
            new_status=self.status,
            reason_text=note,
            changed_by=user,
        )

    def save(self, *args, **kwargs):
        acting_user = kwargs.pop('acting_user', None)
        self.container_number = normalize_container_number(self.container_number)

        if self.status != self.Status.UNAVAILABLE:
            self.unavailability_note = ''

        # Une vérification n'a de sens que pour un conteneur confirmé.
        if self.status != self.Status.CONFIRMED:
            self.verification_result = ''
            self.verification_note = ''
            self.verified_by = None
            self.verified_at = None

        if not self.first_scheduled_date:
            self.first_scheduled_date = self.scheduled_date

        old_status = ''
        old_reason = ''
        is_new = self.pk is None
        if not is_new:
            previous = Container.objects.filter(pk=self.pk).first()
            if previous:
                old_status = previous.status
                old_reason = previous.full_reason_display

        if acting_user and getattr(acting_user, 'is_authenticated', False):
            if is_new and not self.created_by_id:
                self.created_by = acting_user
            self.updated_by = acting_user

        super().save(*args, **kwargs)

        new_reason = self.full_reason_display
        if is_new or old_status != self.status or old_reason != new_reason:
            ContainerStatusLog.objects.create(
                container=self,
                old_status=old_status,
                new_status=self.status,
                reason_text=new_reason,
                changed_by=acting_user or self.updated_by or self.created_by,
            )


class ContainerStatusLog(models.Model):
    class Action(models.TextChoices):
        STATUS_CHANGE = 'status_change', 'Changement de statut'
        VERIFIED = 'verified', 'Vérifié'
        NOT_VERIFIED = 'not_verified', 'Non vérifié'
        VERIFICATION_CLEARED = 'verification_cleared', 'Vérification annulée'
        CARRIED = 'carried', 'Repris dans une nouvelle liste'
        REJECTED = 'rejected', 'Rejeté du suivi'

    action = models.CharField(
        max_length=24,
        choices=Action.choices,
        default=Action.STATUS_CHANGE,
        db_index=True,
        verbose_name="Opération",
    )
    container = models.ForeignKey(
        Container,
        on_delete=models.CASCADE,
        related_name='status_logs',
        verbose_name="Conteneur",
    )
    old_status = models.CharField(
        max_length=16,
        choices=Container.Status.choices,
        blank=True,
        verbose_name="Ancien statut",
    )
    new_status = models.CharField(
        max_length=16,
        choices=Container.Status.choices,
        verbose_name="Nouveau statut",
    )
    reason_text = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Motif / Note",
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='status_changes',
        verbose_name="Modifié par",
    )
    changed_at = models.DateTimeField(auto_now_add=True, verbose_name="Date et heure")

    class Meta:
        db_table = 'container_status_logs'
        verbose_name = "Historique de statut"
        verbose_name_plural = "Historiques de statuts"
        ordering = ['-changed_at']

    def __str__(self):
        old_label = self.get_old_status_display() if self.old_status else "Création"
        return f"{self.container.container_number}: {old_label} → {self.get_new_status_display()}"


class SavedContainer(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='saved_containers',
        verbose_name="Client",
    )
    container_number = models.CharField(
        max_length=11,
        validators=[validate_container_number],
        db_index=True,
        verbose_name="Numéro de conteneur",
    )
    label = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="Référence client / Description",
    )
    notify_whatsapp = models.BooleanField(
        default=True,
        verbose_name="Alerte WhatsApp / SMS souhaitée",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'client_saved_containers'
        verbose_name = "Conteneur suivi (Client)"
        verbose_name_plural = "Conteneurs suivis (Clients)"
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'container_number'],
                name='unique_saved_container_per_user',
            )
        ]

    def save(self, *args, **kwargs):
        self.container_number = normalize_container_number(self.container_number)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.container_number} ({self.user.username})"


class SiteSettings(models.Model):
    """Paramètres et textes publics modifiables depuis le tableau de bord."""

    company_name = models.CharField(
        max_length=120,
        default="Cap Terminal S.A.",
        verbose_name="Nom de l'entreprise",
    )
    portal_title = models.CharField(
        max_length=80,
        default="Portail RAMPE",
        verbose_name="Titre du portail",
    )
    portal_subtitle = models.CharField(
        max_length=160,
        default="Vérification douanière des conteneurs",
        verbose_name="Sous-titre du portail",
    )
    location_label = models.CharField(
        max_length=120,
        default="Port du Cap-Haïtien",
        verbose_name="Localisation (bandeau)",
    )
    address_line = models.CharField(
        max_length=220,
        default="Port du Cap-Haïtien, Département du Nord, Haïti",
        verbose_name="Adresse complète (pied de page)",
    )
    contact_email = models.EmailField(
        default="contact@bendyservilus.com",
        verbose_name="Courriel de contact",
    )
    contact_phone_primary = models.CharField(
        max_length=40,
        default="+50941705257",
        verbose_name="Téléphone principal",
    )
    contact_phone_secondary = models.CharField(
        max_length=40,
        blank=True,
        default="",
        verbose_name="Téléphone secondaire (optionnel)",
    )
    footer_about = models.TextField(
        default="Opérateur du terminal à conteneurs du Port du Cap-Haïtien depuis 1998. Port accrédité par l'OMI et certifié ISPS.",
        verbose_name="Présentation (pied de page)",
    )

    hero_badge = models.CharField(
        max_length=100,
        default="Port du Cap-Haïtien · RAMPE",
        verbose_name="Badge d'accueil",
    )
    hero_title = models.CharField(
        max_length=180,
        default="Vérifiez la disponibilité de votre conteneur",
        verbose_name="Titre principal (accueil)",
    )
    hero_subtitle = models.TextField(
        default="Consultez en temps réel si votre conteneur est programmé et confirmé pour la séance de vérification douanière du jour avant de vous déplacer au terminal.",
        verbose_name="Sous-titre d'accueil",
    )
    search_note = models.CharField(
        max_length=180,
        default="Liste mise à jour chaque jour par Cap Terminal S.A.",
        verbose_name="Mention sous la recherche",
    )
    step1_title = models.CharField(
        max_length=80,
        default="Saisir le numéro",
        verbose_name="Étape 1 — Titre",
    )
    step1_text = models.CharField(
        max_length=220,
        default="Reportez les 4 lettres et 7 chiffres inscrits sur votre connaissement maritime (Bill of Lading).",
        verbose_name="Étape 1 — Description",
    )
    step2_title = models.CharField(
        max_length=80,
        default="Consulter la fiche",
        verbose_name="Étape 2 — Titre",
    )
    step2_text = models.CharField(
        max_length=220,
        default="Visualisez instantanément le statut du jour, la ligne maritime associée et l'heure de mise à jour.",
        verbose_name="Étape 2 — Description",
    )
    step3_title = models.CharField(
        max_length=80,
        default="Se présenter à la RAMPE",
        verbose_name="Étape 3 — Titre",
    )
    step3_text = models.CharField(
        max_length=220,
        default="Déplacez-vous au terminal uniquement lorsque votre conteneur affiche le statut Confirmé.",
        verbose_name="Étape 3 — Description",
    )
    status_guide_title = models.CharField(
        max_length=160,
        default="Signification des statuts de vérification",
        verbose_name="Titre du guide des statuts",
    )
    status_guide_subtitle = models.TextField(
        default="Chaque conteneur programmé passe par l'un des trois états suivants selon son positionnement physique à la RAMPE.",
        verbose_name="Sous-titre du guide des statuts",
    )
    confirmed_title = models.CharField(
        max_length=120,
        default="Conteneur positionné à la RAMPE",
        verbose_name="Titre — Confirmé",
    )
    confirmed_description = models.TextField(
        default="Le conteneur est physiquement disponible et validé par le terminal pour la séance de vérification douanière du jour.",
        verbose_name="Description — Confirmé",
    )
    confirmed_instruction = models.CharField(
        max_length=240,
        default="Présentez-vous au terminal muni de vos documents douaniers.",
        verbose_name="Consigne — Confirmé",
    )
    pending_title = models.CharField(
        max_length=120,
        default="En attente de confirmation",
        verbose_name="Titre — En attente",
    )
    pending_description = models.TextField(
        default="Le conteneur figure sur la programmation quotidienne, mais son positionnement final à la RAMPE n'est pas encore confirmé.",
        verbose_name="Description — En attente",
    )
    pending_instruction = models.CharField(
        max_length=240,
        default="Patientez avant de vous déplacer : la fiche se met à jour automatiquement.",
        verbose_name="Consigne — En attente",
    )
    unavailable_title = models.CharField(
        max_length=120,
        default="Non disponible ce jour",
        verbose_name="Titre — Indisponible",
    )
    unavailable_description = models.TextField(
        default="Le conteneur ne peut pas être présenté à la vérification douanière pour la séance en cours. Le motif exact est indiqué sur sa fiche.",
        verbose_name="Description — Indisponible",
    )
    unavailable_instruction = models.CharField(
        max_length=240,
        default="Consultez le motif affiché et rapprochez-vous de votre transitaire.",
        verbose_name="Consigne — Indisponible",
    )
    client_cta_title = models.CharField(
        max_length=180,
        default="Vous gérez plusieurs dossiers d'importation ou de transit ?",
        verbose_name="Titre bandeau espace client",
    )
    client_cta_subtitle = models.TextField(
        default="Créez un compte client gratuit pour enregistrer vos numéros de conteneurs dans une liste personnelle et suivre leurs statuts en un seul coup d'œil.",
        verbose_name="Texte bandeau espace client",
    )
    # Version créole haïtien (Kreyòl ayisyen) des textes publics
    portal_title_ht = models.CharField(
        max_length=120,
        blank=True,
        default="Pòtay RANP",
        verbose_name="Titre du portail (Kreyòl)",
    )
    portal_subtitle_ht = models.CharField(
        max_length=160,
        blank=True,
        default="Verifikasyon ladwàn pou kontenè yo",
        verbose_name="Sous-titre du portail (Kreyòl)",
    )
    location_label_ht = models.CharField(
        max_length=120,
        blank=True,
        default="Pò Okap",
        verbose_name="Localisation (Kreyòl)",
    )
    address_line_ht = models.CharField(
        max_length=220,
        blank=True,
        default="Pò Okap, Depatman Nò, Ayiti",
        verbose_name="Adresse complète (Kreyòl)",
    )
    footer_about_ht = models.TextField(
        blank=True,
        default="Operatè tèminal kontenè Pò Okap depi 1998. Pò ki sètifye pa OMI ak ISPS.",
        verbose_name="Présentation pied de page (Kreyòl)",
    )
    hero_title_ht = models.CharField(
        max_length=180,
        blank=True,
        default="Verifye si kontenè ou a disponib",
        verbose_name="Titre principal (Kreyòl)",
    )
    hero_subtitle_ht = models.TextField(
        blank=True,
        default="Konsilte an tan reyèl si kontenè ou a pwograme epi konfime pou verifikasyon ladwàn jodi a anvan ou deplase vin nan tèminal la.",
        verbose_name="Sous-titre d'accueil (Kreyòl)",
    )
    search_note_ht = models.CharField(
        max_length=180,
        blank=True,
        default="Cap Terminal S.A. mete lis la ajou chak jou.",
        verbose_name="Mention sous la recherche (Kreyòl)",
    )
    step1_title_ht = models.CharField(
        max_length=80,
        blank=True,
        default="Antre nimewo a",
        verbose_name="Étape 1 — Titre (Kreyòl)",
    )
    step1_text_ht = models.CharField(
        max_length=220,
        blank=True,
        default="Ekri 4 lèt ak 7 chif ki sou konèsman maritim ou a (Bill of Lading).",
        verbose_name="Étape 1 — Description (Kreyòl)",
    )
    step2_title_ht = models.CharField(
        max_length=80,
        blank=True,
        default="Gade fich la",
        verbose_name="Étape 2 — Titre (Kreyòl)",
    )
    step2_text_ht = models.CharField(
        max_length=220,
        blank=True,
        default="Wè touswit stati jodi a, liy maritim nan ak lè dènye mizajou a.",
        verbose_name="Étape 2 — Description (Kreyòl)",
    )
    step3_title_ht = models.CharField(
        max_length=80,
        blank=True,
        default="Ale nan RANP la",
        verbose_name="Étape 3 — Titre (Kreyòl)",
    )
    step3_text_ht = models.CharField(
        max_length=220,
        blank=True,
        default="Vin nan tèminal la sèlman lè kontenè ou a gen stati Konfime.",
        verbose_name="Étape 3 — Description (Kreyòl)",
    )
    status_guide_title_ht = models.CharField(
        max_length=160,
        blank=True,
        default="Kisa stati verifikasyon yo vle di",
        verbose_name="Titre du guide des statuts (Kreyòl)",
    )
    status_guide_subtitle_ht = models.TextField(
        blank=True,
        default="Chak kontenè ki pwograme pase nan youn nan twa eta sa yo selon plas li nan RANP la.",
        verbose_name="Sous-titre du guide des statuts (Kreyòl)",
    )
    confirmed_title_ht = models.CharField(
        max_length=120,
        blank=True,
        default="Kontenè a plase nan RANP la",
        verbose_name="Titre — Confirmé (Kreyòl)",
    )
    confirmed_description_ht = models.TextField(
        blank=True,
        default="Kontenè a disponib fizikman epi tèminal la valide li pou verifikasyon ladwàn jodi a.",
        verbose_name="Description — Confirmé (Kreyòl)",
    )
    confirmed_instruction_ht = models.CharField(
        max_length=240,
        blank=True,
        default="Vin nan tèminal la ak tout dokiman ladwàn ou yo.",
        verbose_name="Consigne — Confirmé (Kreyòl)",
    )
    pending_title_ht = models.CharField(
        max_length=120,
        blank=True,
        default="Ap tann konfimasyon",
        verbose_name="Titre — En attente (Kreyòl)",
    )
    pending_description_ht = models.TextField(
        blank=True,
        default="Kontenè a nan pwogramasyon jodi a, men plasman final li nan RANP la poko konfime.",
        verbose_name="Description — En attente (Kreyòl)",
    )
    pending_instruction_ht = models.CharField(
        max_length=240,
        blank=True,
        default="Pasyante anvan ou deplase : fich la ap mete tèt li ajou otomatikman.",
        verbose_name="Consigne — En attente (Kreyòl)",
    )
    unavailable_title_ht = models.CharField(
        max_length=120,
        blank=True,
        default="Pa disponib jodi a",
        verbose_name="Titre — Indisponible (Kreyòl)",
    )
    unavailable_description_ht = models.TextField(
        blank=True,
        default="Kontenè a pa ka prezante pou verifikasyon ladwàn jodi a. Rezon an ekri sou fich li.",
        verbose_name="Description — Indisponible (Kreyòl)",
    )
    unavailable_instruction_ht = models.CharField(
        max_length=240,
        blank=True,
        default="Gade rezon ki afiche a epi kontakte tranzitè ou a.",
        verbose_name="Consigne — Indisponible (Kreyòl)",
    )
    client_cta_title_ht = models.CharField(
        max_length=180,
        blank=True,
        default="Ou jere plizyè dosye enpòtasyon oswa tranzit ?",
        verbose_name="Titre bandeau espace client (Kreyòl)",
    )
    client_cta_subtitle_ht = models.TextField(
        blank=True,
        default="Kreye yon kont kliyan gratis pou anrejistre nimewo kontenè ou yo nan yon lis pèsonèl epi swiv stati yo fasilman.",
        verbose_name="Texte bandeau espace client (Kreyòl)",
    )
    hero_carousel_enabled = models.BooleanField(
        default=True,
        verbose_name="Activer le carrousel en fond du hero",
    )
    hero_carousel_limit = models.PositiveSmallIntegerField(
        default=4,
        validators=[MinValueValidator(1), MaxValueValidator(6)],
        verbose_name="Nombre maximum d'images affichées",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'site_settings'
        verbose_name = "Paramètres du site"
        verbose_name_plural = "Paramètres du site"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def for_language(self, lang_code: str):
        return LocalizedSiteSettings(self, lang_code)

    def __str__(self):
        return f"Paramètres du site ({self.company_name})"


class LocalizedSiteSettings:
    """Renvoie la version créole (suffixe `_ht`) lorsqu'elle est définie et que la langue est `ht`."""

    def __init__(self, instance: SiteSettings, lang_code: str):
        self._instance = instance
        self._is_ht = (lang_code or '').startswith('ht')

    def __getattr__(self, name: str):
        if self._is_ht:
            ht_attr = f"{name}_ht"
            if hasattr(self._instance, ht_attr):
                val = getattr(self._instance, ht_attr)
                if val:
                    return val
        return getattr(self._instance, name)


class HeroSlide(models.Model):
    """Image d'arrière-plan du carrousel d'accueil, gérée depuis le portail."""

    title = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="Légende / description",
    )
    image = models.ImageField(
        upload_to='hero_slides/',
        verbose_name="Image",
    )
    display_order = models.PositiveSmallIntegerField(
        default=1,
        verbose_name="Ordre d'affichage",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Active",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'hero_slides'
        ordering = ['display_order', 'id']
        verbose_name = "Image du carrousel d'accueil"
        verbose_name_plural = "Images du carrousel d'accueil"

    def __str__(self):
        return self.title or f"Image #{self.pk}"

    def save(self, *args, **kwargs):
        if self.image and not getattr(self.image, '_committed', True):
            self._optimize_image()
        super().save(*args, **kwargs)

    def _optimize_image(self):
        """Redimensionne (max 1600 px) et compresse en WebP pour le chargement rapide."""
        try:
            self.image.seek(0)
            img = ImageOps.exif_transpose(Image.open(self.image))
            if img.mode not in ('RGB', 'RGBA'):
                img = img.convert('RGB')
            max_width = 1600
            if img.width > max_width:
                ratio = max_width / float(img.width)
                img = img.resize((max_width, int(img.height * ratio)), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format='WEBP', quality=82, method=4)
            base_name = os.path.splitext(os.path.basename(self.image.name))[0] or 'slide'
            self.image.save(f"{base_name}.webp", ContentFile(buf.getvalue()), save=False)
        except Exception:
            pass


