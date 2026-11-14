import re
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

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

