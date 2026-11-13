from django.contrib.auth.models import AbstractUser
from django.db import models

# Create your models here.


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Administrateur'
        SUPERVISOR = 'supervisor', 'Superviseur'
        CLIENT = 'client', 'Client'

    role = models.CharField(
        max_length=25,
        choices=Role.choices,
        default=Role.CLIENT,
        verbose_name='Rôle',
    )
    full_name = models.CharField(
        max_length=120,
        blank=True,
        verbose_name='Nom complet',
    )
    company_name = models.CharField(
        max_length=120,
        blank=True,
        verbose_name='Entreprise / Agence en douane',
    )
    phone = models.CharField(
        max_length=30,
        blank=True,
        verbose_name='Téléphone / WhatsApp',
    )

    class Meta:
        verbose_name = 'Utilisateur'
        verbose_name_plural = 'Utilisateurs'
        ordering = ['username']

    def __str__(self):
        display = self.full_name or self.get_full_name() or self.username
        return f"{display} ({self.get_role_display()})"

    @property
    def display_name(self) -> str:
        return self.full_name or self.get_full_name() or self.username

    @property
    def is_portal_admin(self) -> bool:
        return self.is_superuser or self.role == self.Role.ADMIN

    @property
    def is_supervisor(self) -> bool:
        return self.role == self.Role.SUPERVISOR

    @property
    def is_staff_member(self) -> bool:
        return self.is_portal_admin or self.is_supervisor

    @property
    def can_mark_verification(self) -> bool:
        # Administrateur et Superviseur peuvent marquer la vérification (Vérifié / Non vérifié).
        return self.is_staff_member

    @property
    def can_edit_or_delete_containers(self) -> bool:
        # Droits compilés pour Administrateur et Superviseur.
        return self.is_staff_member

