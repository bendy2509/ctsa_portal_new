import io
import os
import re
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
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


class Motif(models.Model):
    """Motif prédéfini d'indisponibilité ou de non-vérification.

    Jamais supprimé (on_delete=PROTECT sur les références) : désactiver via `is_active=False`.
    `is_active` contrôle la sélection pour les nouvelles saisies (fusion de actif et sélectionnable).
    `is_system=True` protège les motifs système (ex. « Non renseigné (historique) »).
    """

    class Type(models.TextChoices):
        UNAVAILABLE = 'unavailable', 'Indisponibilité'
        NOT_VERIFIED = 'not_verified', 'Non-vérification'

    type = models.CharField(
        max_length=20,
        choices=Type.choices,
        db_index=True,
        verbose_name="Type de motif",
    )
    label = models.CharField(max_length=160, verbose_name="Libellé")
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="Actif (sélectionnable)",
    )
    is_system = models.BooleanField(
        default=False,
        verbose_name="Motif système",
    )
    display_order = models.PositiveSmallIntegerField(
        default=0,
        verbose_name="Ordre d'affichage",
    )
    requires_note = models.BooleanField(
        default=False,
        verbose_name="Exige une précision",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'motifs'
        verbose_name = "Motif"
        verbose_name_plural = "Motifs"
        ordering = ['type', 'display_order', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['type', 'label'],
                name='unique_motif_type_label',
            )
        ]

    def delete(self, *args, **kwargs):
        raise ValidationError("Un motif ne peut jamais être supprimé ; désactivez-le à la place.")

    def __str__(self):
        return f"{self.label} ({self.get_type_display()})"


class ActiveScheduledContainerManager(models.Manager):
    """Gestionnaire par défaut : exclut les conteneurs archivés et les conteneurs « Enregistré » (hors liste)."""

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(archived_at__isnull=True)
            .exclude(status=Container.Status.REGISTERED)
        )


class RegisteredContainerManager(models.Manager):
    """Conteneurs arrivés sans liste, non archivés."""

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(archived_at__isnull=True, status=Container.Status.REGISTERED)
        )


class Container(models.Model):
    class Status(models.TextChoices):
        REGISTERED = 'registered', 'Enregistré'
        CONFIRMED = 'confirmed', 'Confirmé'
        PENDING = 'pending', 'En attente'
        UNAVAILABLE = 'unavailable', 'Indisponible'

    class Origin(models.TextChoices):
        PROGRAMME = 'programme', 'Programmé'
        ARRIVE_SANS_LISTE = 'arrive_sans_liste', 'Arrivé sans liste'

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
    normalized_number = models.CharField(
        max_length=11,
        db_index=True,
        editable=False,
        default='',
        verbose_name="Numéro normalisé",
    )
    origin = models.CharField(
        max_length=24,
        choices=Origin.choices,
        default=Origin.PROGRAMME,
        db_index=True,
        verbose_name="Origine",
    )
    arrived_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Arrivé le",
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
        null=True,
        blank=True,
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
    unavailability_reason = models.ForeignKey(
        Motif,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='unavailable_containers',
        verbose_name="Motif d'indisponibilité",
    )
    unavailability_note = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Précision d'indisponibilité",
    )
    # --- Résultat de la séance de vérification douanière (dérivé de VerificationEvent) ---
    verification_result = models.CharField(
        max_length=16,
        choices=Verification.choices,
        blank=True,
        default='',
        db_index=True,
        verbose_name="Vérification",
    )
    verification_reason = models.ForeignKey(
        Motif,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='not_verified_containers',
        verbose_name="Motif de non-vérification",
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
    version = models.PositiveIntegerField(
        default=1,
        verbose_name="Version (concurrence)",
    )

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
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="Archivé le")
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='archived_containers',
        verbose_name="Archivé par",
    )
    archive_reason = models.CharField(max_length=255, blank=True, verbose_name="Motif d'archivage")

    objects = ActiveScheduledContainerManager()
    all_objects = models.Manager()
    registered_objects = RegisteredContainerManager()

    class Meta:
        db_table = 'containers'
        base_manager_name = 'all_objects'
        verbose_name = "Conteneur programmé"
        verbose_name_plural = "Conteneurs programmés"
        ordering = ['-scheduled_date', 'container_number']
        indexes = [
            models.Index(fields=['normalized_number', 'status', 'archived_at'], name='idx_cont_norm_status_arch'),
            models.Index(fields=['scheduled_date', 'status', 'archived_at'], name='idx_cont_sched_status_arch'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['container_number', 'scheduled_date'],
                name='unique_container_per_day',
            ),
            models.UniqueConstraint(
                fields=['normalized_number'],
                condition=models.Q(status='registered', archived_at__isnull=True),
                name='unique_active_registered_container',
            ),
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
        if self.unavailability_reason_id:
            label = self.unavailability_reason.label
            if self.unavailability_note:
                return f"{label} — {self.unavailability_note}"
            return label
        return self.unavailability_note or "Motif non précisé"

    @property
    def is_verified(self) -> bool:
        return self.verification_result == self.Verification.VERIFIED

    @property
    def can_be_verified(self) -> bool:
        """Seul un conteneur confirmé, non archivé, dont la séance a commencé, peut être marqué."""
        return (
            self.archived_at is None
            and self.status == self.Status.CONFIRMED
            and self.scheduled_date is not None
            and self.scheduled_date <= timezone.localdate()
        )

    @property
    def needs_carry_decision(self) -> bool:
        """Conteneur « à suivre » : programmé, séance passée, ni vérifié, ni repris, ni rejeté."""
        return (
            self.archived_at is None
            and self.status != self.Status.REGISTERED
            and not self.is_verified
            and not self.carry_decision
            and self.scheduled_date is not None
            and self.scheduled_date < timezone.localdate()
        )

    @property
    def days_since_first_scheduled(self) -> int:
        start = self.first_scheduled_date or self.scheduled_date
        if start is None:
            return 0
        return max((timezone.localdate() - start).days, 0)

    ALLOWED_STATUS_TRANSITIONS = {
        Status.REGISTERED: {Status.REGISTERED, Status.PENDING},
        Status.PENDING: {Status.PENDING, Status.CONFIRMED, Status.UNAVAILABLE},
        Status.CONFIRMED: {Status.CONFIRMED, Status.PENDING, Status.UNAVAILABLE},
        Status.UNAVAILABLE: {Status.UNAVAILABLE, Status.PENDING, Status.CONFIRMED},
    }

    def can_change_status(self, new_status: str):
        """Vérifie si la transition vers `new_status` est autorisée par la machine à états."""
        if self.archived_at is not None:
            return False, "Un conteneur archivé ne peut pas changer de statut."
        if new_status not in dict(self.Status.choices):
            return False, "Statut cible inconnu."
        if self.is_verified:
            return False, (
                "Ce conteneur est déjà vérifié et verrouillé. "
                "Corrigez ou annulez d'abord la vérification."
            )
        if self.verification_result == self.Verification.NOT_VERIFIED and new_status != self.status:
            return False, (
                "Ce conteneur a un résultat « Non vérifié ». "
                "Corrigez ou supprimez d'abord le mouvement de vérification."
            )
        allowed = self.ALLOWED_STATUS_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            if self.status == self.Status.REGISTERED and new_status in (
                self.Status.CONFIRMED,
                self.Status.UNAVAILABLE,
            ):
                return False, (
                    "Un conteneur enregistré hors liste doit d'abord être ajouté à une liste "
                    "(En attente) avant d'être confirmé ou marqué indisponible."
                )
            return False, "Transition de statut non autorisée."
        return True, ""

    def change_status(
        self,
        new_status: str,
        acting_user=None,
        reason=None,
        note: str = '',
        expected_version=None,
    ):
        """Applique une transition de disponibilité dans une transaction atomique."""
        with transaction.atomic():
            if expected_version is not None and self.version != expected_version:
                raise ValidationError(
                    "Conflit de concurrence : ce conteneur a été modifié entre-temps. Actualisez la page."
                )
            ok, err = self.can_change_status(new_status)
            if not ok:
                raise ValidationError(err)

            self.status = new_status
            if new_status == self.Status.UNAVAILABLE:
                self.unavailability_reason = reason
                self.unavailability_note = (note or '')[:255]
            else:
                self.unavailability_reason = None
                self.unavailability_note = ''
            self.version = (self.version or 1) + 1
            self.save(acting_user=acting_user)

    def remove_from_list(self, acting_user=None, reason: str = ''):
        """Retire un conteneur de sa liste : retour à Enregistré (arrive_sans_liste) ou archivage (programme)."""
        if self.archived_at is not None:
            raise ValidationError("Ce conteneur est déjà archivé.")
        if self.status == self.Status.REGISTERED:
            raise ValidationError("Ce conteneur n'est sur aucune liste.")
        if self.is_verified:
            raise ValidationError(
                "Un conteneur vérifié est verrouillé et ne peut pas être retiré directement."
            )
        user = acting_user if getattr(acting_user, 'is_authenticated', False) else None
        with transaction.atomic():
            if self.carried_from_id:
                Container.all_objects.filter(
                    pk=self.carried_from_id,
                    carry_decision=self.CarryDecision.CARRIED,
                ).update(carry_decision='', carry_decided_by=None, carry_decided_at=None)

            if self.origin == self.Origin.ARRIVE_SANS_LISTE:
                old_status = self.status
                old_date = self.scheduled_date
                self.status = self.Status.REGISTERED
                self.scheduled_date = None
                self.first_scheduled_date = None
                self.unavailability_reason = None
                self.unavailability_note = ''
                self.carried_from = None
                self.carry_decision = ''
                self.carry_decided_by = None
                self.carry_decided_at = None
                self.version = (self.version or 1) + 1
                self.save(acting_user=user, skip_status_log=True)
                ContainerStatusLog.objects.create(
                    container=self,
                    action=ContainerStatusLog.Action.STATUS_CHANGE,
                    old_status=old_status,
                    new_status=self.Status.REGISTERED,
                    reason_text=(
                        f"Retiré de la liste du {old_date.strftime('%d/%m/%Y')} → retour à Enregistré"
                        if old_date
                        else "Retiré de la liste → retour à Enregistré"
                    ),
                    changed_by=user,
                )
            else:
                archive_reason = (reason or '').strip() or "Retiré de la liste"
                self.archived_at = timezone.now()
                self.archived_by = user
                self.archive_reason = archive_reason[:255]
                self.version = (self.version or 1) + 1
                self.save(acting_user=user, skip_status_log=True)
                ContainerStatusLog.objects.create(
                    container=self,
                    action=ContainerStatusLog.Action.CONTAINER_ARCHIVED,
                    old_status=self.status,
                    new_status=self.status,
                    reason_text=self.archive_reason,
                    changed_by=user,
                )

    def archive(self, acting_user=None, reason: str = ''):
        """Archive un conteneur Enregistré (hors liste) sans suppression physique."""
        if self.archived_at is not None:
            raise ValidationError("Ce conteneur est déjà archivé.")
        if self.is_verified:
            raise ValidationError(
                "Un conteneur vérifié ne peut pas être archivé directement. Annulez d'abord la vérification."
            )
        if self.status != self.Status.REGISTERED:
            raise ValidationError(
                "Un conteneur sur une liste ne peut pas être archivé directement : retirez-le d'abord de la liste."
            )
        clean_reason = (reason or '').strip() or "Archivage manuel"
        user = acting_user if getattr(acting_user, 'is_authenticated', False) else None
        with transaction.atomic():
            self.archived_at = timezone.now()
            self.archived_by = user
            self.archive_reason = clean_reason[:255]
            self.version = (self.version or 1) + 1
            self.save(acting_user=user, skip_status_log=True)
            ContainerStatusLog.objects.create(
                container=self,
                action=ContainerStatusLog.Action.CONTAINER_ARCHIVED,
                old_status=self.status,
                new_status=self.status,
                reason_text=self.archive_reason,
                changed_by=user,
            )

    def restore(self, acting_user=None, reason: str = ''):
        """Restaure un conteneur archivé (réservé Superadmin)."""
        if self.archived_at is None:
            raise ValidationError("Ce conteneur n'est pas archivé.")
        user = acting_user if getattr(acting_user, 'is_authenticated', False) else None
        clean_reason = (reason or '').strip() or "Restauration Superadmin"
        with transaction.atomic():
            self.archived_at = None
            self.archived_by = None
            self.archive_reason = ''
            self.version = (self.version or 1) + 1
            self.save(acting_user=user, skip_status_log=True)
            ContainerStatusLog.objects.create(
                container=self,
                action=ContainerStatusLog.Action.CONTAINER_RESTORED,
                old_status=self.status,
                new_status=self.status,
                reason_text=clean_reason[:255],
                changed_by=user,
            )
            PortalAuditLog.objects.create(
                category=PortalAuditLog.Category.ARCHIVE_RESTORE,
                target_label=f"Container:{self.normalized_number}",
                old_value="archived",
                new_value=f"active ({self.status})",
                reason=clean_reason[:255],
                changed_by=user,
            )

    def set_verification(
        self,
        result: str,
        acting_user=None,
        note: str = '',
        reason=None,
        expected_version=None,
    ):
        """Marque le conteneur Vérifié / Non vérifié ('' pour annuler) via VerificationEvent atomique."""
        if expected_version is not None and self.version != expected_version:
            raise ValidationError(
                "Conflit de concurrence : ce conteneur a été modifié par un autre utilisateur. Veuillez actualiser."
            )
        if result not in ('', *self.Verification.values):
            raise ValidationError("Résultat de vérification inconnu.")
        if result and not self.can_be_verified:
            raise ValidationError(
                "Seul un conteneur confirmé, dont la séance a commencé, peut être marqué vérifié ou non vérifié."
            )
        if (
            self.verification_result == self.Verification.NOT_VERIFIED
            and self.carry_decision in (self.CarryDecision.CARRIED, self.CarryDecision.REJECTED)
            and result != self.verification_result
        ):
            raise ValidationError(
                "Impossible de corriger ou supprimer le mouvement d'un conteneur déjà repris ou rejeté."
            )
        note = (note or '')[:255] if result == self.Verification.NOT_VERIFIED else ''
        reason_obj = reason if result == self.Verification.NOT_VERIFIED else None
        reason_id = reason_obj.pk if reason_obj else None
        if (
            result == self.verification_result
            and note == self.verification_note
            and reason_id == self.verification_reason_id
        ):
            return
        user = acting_user if getattr(acting_user, 'is_authenticated', False) else None
        with transaction.atomic():
            if result == '':
                # Annulation / remise à Non inspecté : archive les événements actifs
                VerificationEvent.objects.filter(
                    container=self, archived_at__isnull=True
                ).update(
                    archived_at=timezone.now(),
                    archived_by=user,
                    archive_reason=note or "Marquage annulé",
                )
            else:
                VerificationEvent.objects.create(
                    container=self,
                    result=result,
                    reason=reason_obj,
                    note=note,
                    created_by=user,
                    created_at=timezone.now(),
                )
            sync_container_verification(self, acting_user=user)
            log_reason = note
            if reason_obj:
                log_reason = f"{reason_obj.label} — {note}" if note else reason_obj.label
            ContainerStatusLog.objects.create(
                container=self,
                action=result or ContainerStatusLog.Action.VERIFICATION_CLEARED,
                old_status=self.status,
                new_status=self.status,
                reason_text=log_reason[:255],
                changed_by=user,
            )

    def save(self, *args, **kwargs):
        acting_user = kwargs.pop('acting_user', None)
        skip_status_log = kwargs.pop('skip_status_log', False)
        self.container_number = normalize_container_number(self.container_number)
        self.normalized_number = self.container_number

        if self.status == self.Status.REGISTERED:
            self.scheduled_date = None
            self.first_scheduled_date = None
            if not self.arrived_at:
                self.arrived_at = timezone.now()

        if self.status != self.Status.UNAVAILABLE:
            self.unavailability_reason = None
            self.unavailability_note = ''

        # Une vérification n'a de sens que pour un conteneur confirmé.
        if self.status != self.Status.CONFIRMED:
            self.verification_result = ''
            self.verification_reason = None
            self.verification_note = ''
            self.verified_by = None
            self.verified_at = None

        if not self.first_scheduled_date and self.scheduled_date is not None:
            self.first_scheduled_date = self.scheduled_date

        old_status = ''
        old_reason = ''
        is_new = self.pk is None
        if not is_new:
            previous = Container.all_objects.filter(pk=self.pk).first()
            if previous:
                old_status = previous.status
                old_reason = previous.full_reason_display

        if acting_user and getattr(acting_user, 'is_authenticated', False):
            if is_new and not self.created_by_id:
                self.created_by = acting_user
            self.updated_by = acting_user

        super().save(*args, **kwargs)

        new_reason = self.full_reason_display
        if not skip_status_log and (is_new or old_status != self.status or old_reason != new_reason):
            action = ContainerStatusLog.Action.STATUS_CHANGE
            if is_new and self.status == self.Status.REGISTERED:
                action = ContainerStatusLog.Action.ARRIVED
            ContainerStatusLog.objects.create(
                container=self,
                action=action,
                old_status=old_status,
                new_status=self.status,
                reason_text=new_reason,
                changed_by=acting_user or self.updated_by or self.created_by,
            )


def sync_container_verification(container: Container, acting_user=None) -> Container:
    """Source de vérité : dérive l'état d'inspection du dernier VerificationEvent non archivé."""
    with transaction.atomic():
        latest = (
            VerificationEvent.objects.select_related('reason')
            .filter(container=container, archived_at__isnull=True)
            .order_by('-created_at', '-pk')
            .first()
        )
        if latest is None:
            container.verification_result = ''
            container.verification_reason = None
            container.verification_note = ''
            container.verified_by = None
            container.verified_at = None
        else:
            container.verification_result = latest.result
            container.verification_reason = latest.reason if latest.result == Container.Verification.NOT_VERIFIED else None
            container.verification_note = latest.note if latest.result == Container.Verification.NOT_VERIFIED else ''
            container.verified_by = latest.created_by
            container.verified_at = latest.created_at
        container.version = (container.version or 1) + 1
        container.save(acting_user=acting_user, skip_status_log=True)
    return container


class VerificationEvent(models.Model):
    """Événement d'inspection (source de vérité pour verification_result)."""

    container = models.ForeignKey(
        Container,
        on_delete=models.CASCADE,
        related_name='verification_events',
        verbose_name="Conteneur",
    )
    result = models.CharField(
        max_length=16,
        choices=Container.Verification.choices,
        db_index=True,
        verbose_name="Résultat",
    )
    reason = models.ForeignKey(
        Motif,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='verification_events',
        verbose_name="Motif",
    )
    note = models.CharField(max_length=255, blank=True, verbose_name="Précision")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_verification_events',
        verbose_name="Marqué par",
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True, verbose_name="Marqué le")
    corrected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='corrected_verification_events',
        verbose_name="Corrigé par",
    )
    corrected_at = models.DateTimeField(null=True, blank=True, verbose_name="Corrigé le")
    is_historical = models.BooleanField(
        default=False,
        verbose_name="Historique (migré, hors fenêtre)",
    )
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="Archivé le")
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='archived_verification_events',
        verbose_name="Archivé par",
    )
    archive_reason = models.CharField(max_length=255, blank=True, verbose_name="Motif d'archivage")

    class Meta:
        db_table = 'verification_events'
        verbose_name = "Événement de vérification"
        verbose_name_plural = "Événements de vérification"
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return f"{self.container.container_number} — {self.get_result_display()}"


class ContainerStatusLog(models.Model):
    class Action(models.TextChoices):
        STATUS_CHANGE = 'status_change', 'Changement de statut'
        ARRIVED = 'arrived', 'Arrivée enregistrée'
        ADDED_TO_LIST = 'added_to_list', 'Ajouté à une liste'
        VERIFIED = 'verified', 'Vérifié'
        NOT_VERIFIED = 'not_verified', 'Non vérifié'
        VERIFICATION_CLEARED = 'verification_cleared', 'Vérification annulée'
        VERIFICATION_CORRECTED = 'verification_corrected', 'Vérification corrigée'
        VERIFICATION_ARCHIVED = 'verification_archived', 'Mouvement de vérification archivé'
        CARRIED = 'carried', 'Repris dans une nouvelle liste'
        REJECTED = 'rejected', 'Rejeté du suivi'
        CONTAINER_ARCHIVED = 'container_archived', 'Conteneur archivé'
        CONTAINER_RESTORED = 'container_restored', 'Conteneur restauré'

    action = models.CharField(
        max_length=32,
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
    correction_window_minutes = models.PositiveSmallIntegerField(
        default=60,
        validators=[MinValueValidator(1), MaxValueValidator(1440)],
        verbose_name="Fenêtre de correction d'inspection (minutes)",
    )
    delete_window_hours = models.PositiveSmallIntegerField(
        default=24,
        validators=[MinValueValidator(1), MaxValueValidator(168)],
        verbose_name="Fenêtre de suppression Superviseur (heures)",
    )
    unscheduled_alert_days = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(90)],
        verbose_name="Seuil d'alerte conteneur sans liste (jours)",
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


class PortalAuditLog(models.Model):
    """Journal d'audit en ajout seulement : rôles, paramètres, promotions et restaurations."""

    class Category(models.TextChoices):
        ROLE_CHANGE = 'role_change', 'Changement de rôle'
        SETTING_CHANGE = 'setting_change', 'Modification de paramètre'
        ARCHIVE_RESTORE = 'archive_restore', 'Archivage / Restauration'
        PROMOTE_SUPERADMIN = 'promote_superadmin', 'Promotion Superadmin'

    category = models.CharField(
        max_length=24,
        choices=Category.choices,
        db_index=True,
        verbose_name="Catégorie",
    )
    target_label = models.CharField(max_length=160, verbose_name="Objet concerné")
    old_value = models.TextField(blank=True, verbose_name="Ancienne valeur")
    new_value = models.TextField(blank=True, verbose_name="Nouvelle valeur")
    reason = models.CharField(max_length=255, blank=True, verbose_name="Motif")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='portal_audit_logs',
        verbose_name="Auteur",
    )
    changed_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Date et heure")

    class Meta:
        db_table = 'portal_audit_logs'
        verbose_name = "Journal d'audit"
        verbose_name_plural = "Journaux d'audit"
        ordering = ['-changed_at', '-pk']

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("Le journal d'audit est en ajout seulement et ne peut pas être modifié.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Le journal d'audit est en ajout seulement et ne peut pas être supprimé.")

    def __str__(self):
        return f"[{self.get_category_display()}] {self.target_label}"



