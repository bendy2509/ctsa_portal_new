"""Paramètres du site, carrousel d'accueil et motifs."""

import csv
import datetime
import hashlib
import re

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.permissions import Actions, peut, render_403, require_permission
from accounts.utils import safe_next_url
from terminal.models import (
    ARRIVAL_NUMBER_REGEX,
    CONTAINER_NUMBER_REGEX,
    Container,
    ContainerPrefix,
    ContainerStatusLog,
    HeroSlide,
    Motif,
    PortalAuditLog,
    SavedContainer,
    ShippingLine,
    SiteSettings,
    VerificationEvent,
    is_valid_iso6346,
    normalize_container_number,
    sync_container_verification,
)

from terminal.services.containers import (
    PAGE_SIZE,
    _backlog_breakdown,
    _carry_into,
    _close_tracking,
    _dashboard_url,
    _paginate,
    _parse_date,
    _pick_relevant_schedule,
    _resolve_motif,
    _safe_next,
    default_carry_target,
    default_work_date,
    unverified_backlog,
)



class SiteSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = [
            'company_name',
            'portal_title',
            'portal_subtitle',
            'location_label',
            'address_line',
            'contact_email',
            'contact_phone_primary',
            'contact_phone_secondary',
            'footer_about',
            'hero_badge',
            'hero_title',
            'hero_subtitle',
            'search_note',
            'step1_title',
            'step1_text',
            'step2_title',
            'step2_text',
            'step3_title',
            'step3_text',
            'status_guide_title',
            'status_guide_subtitle',
            'confirmed_title',
            'confirmed_description',
            'confirmed_instruction',
            'pending_title',
            'pending_description',
            'pending_instruction',
            'unavailable_title',
            'unavailable_description',
            'unavailable_instruction',
            'client_cta_title',
            'client_cta_subtitle',
            'hero_carousel_enabled',
            'hero_carousel_limit',
            'correction_window_minutes',
            'delete_window_hours',
            'unscheduled_alert_days',
            # Champs Kreyòl
            'portal_title_ht',
            'portal_subtitle_ht',
            'location_label_ht',
            'address_line_ht',
            'footer_about_ht',
            'hero_title_ht',
            'hero_subtitle_ht',
            'search_note_ht',
            'step1_title_ht',
            'step1_text_ht',
            'step2_title_ht',
            'step2_text_ht',
            'step3_title_ht',
            'step3_text_ht',
            'status_guide_title_ht',
            'status_guide_subtitle_ht',
            'confirmed_title_ht',
            'confirmed_description_ht',
            'confirmed_instruction_ht',
            'pending_title_ht',
            'pending_description_ht',
            'pending_instruction_ht',
            'unavailable_title_ht',
            'unavailable_description_ht',
            'unavailable_instruction_ht',
            'client_cta_title_ht',
            'client_cta_subtitle_ht',
        ]
        widgets = {
            'footer_about': forms.Textarea(attrs={'rows': 4}),
            'hero_subtitle': forms.Textarea(attrs={'rows': 4}),
            'step1_text': forms.Textarea(attrs={'rows': 3}),
            'step2_text': forms.Textarea(attrs={'rows': 3}),
            'step3_text': forms.Textarea(attrs={'rows': 3}),
            'status_guide_subtitle': forms.Textarea(attrs={'rows': 3}),
            'confirmed_description': forms.Textarea(attrs={'rows': 3}),
            'pending_description': forms.Textarea(attrs={'rows': 3}),
            'unavailable_description': forms.Textarea(attrs={'rows': 3}),
            'client_cta_subtitle': forms.Textarea(attrs={'rows': 3}),
            'footer_about_ht': forms.Textarea(attrs={'rows': 4}),
            'hero_subtitle_ht': forms.Textarea(attrs={'rows': 4}),
            'step1_text_ht': forms.Textarea(attrs={'rows': 3}),
            'step2_text_ht': forms.Textarea(attrs={'rows': 3}),
            'step3_text_ht': forms.Textarea(attrs={'rows': 3}),
            'status_guide_subtitle_ht': forms.Textarea(attrs={'rows': 3}),
            'confirmed_description_ht': forms.Textarea(attrs={'rows': 3}),
            'pending_description_ht': forms.Textarea(attrs={'rows': 3}),
            'unavailable_description_ht': forms.Textarea(attrs={'rows': 3}),
            'client_cta_subtitle_ht': forms.Textarea(attrs={'rows': 3}),
            'hero_carousel_limit': forms.NumberInput(attrs={'min': 1, 'max': 6}),
            'correction_window_minutes': forms.NumberInput(attrs={'min': 1, 'max': 1440}),
            'delete_window_hours': forms.NumberInput(attrs={'min': 1, 'max': 168}),
            'unscheduled_alert_days': forms.NumberInput(attrs={'min': 1, 'max': 90}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['hero_badge'].required = False
        for opt_field in ('correction_window_minutes', 'delete_window_hours', 'unscheduled_alert_days'):
            self.fields[opt_field].required = False
        for name, field in self.fields.items():
            if name.endswith('_ht'):
                field.required = False
            if name == 'hero_carousel_enabled':
                field.widget.attrs.setdefault('class', 'w-4 h-4 rounded border-slate-300 text-ctsa-blue focus:ring-ctsa-blue')
            else:
                field.widget.attrs.setdefault('class', 'form-control')

    def clean_correction_window_minutes(self):
        val = self.cleaned_data.get('correction_window_minutes')
        if val is None:
            return getattr(self.instance, 'correction_window_minutes', 60) or 60
        return val

    def clean_delete_window_hours(self):
        val = self.cleaned_data.get('delete_window_hours')
        if val is None:
            return getattr(self.instance, 'delete_window_hours', 24) or 24
        return val

    def clean_unscheduled_alert_days(self):
        val = self.cleaned_data.get('unscheduled_alert_days')
        if val is None:
            return getattr(self.instance, 'unscheduled_alert_days', 3) or 3
        return val


class HeroSlideForm(forms.ModelForm):
    class Meta:
        model = HeroSlide
        fields = ['image', 'title', 'display_order', 'is_active']
        widgets = {
            'image': forms.ClearableFileInput(attrs={'accept': 'image/*'}),
            'title': forms.TextInput(attrs={'placeholder': 'Ex. Vue aérienne du terminal RAMPE'}),
            'display_order': forms.NumberInput(attrs={'min': 1, 'max': 99}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == 'is_active':
                field.widget.attrs.setdefault('class', 'w-4 h-4 rounded border-slate-300 text-ctsa-blue focus:ring-ctsa-blue')
            else:
                field.widget.attrs.setdefault('class', 'form-control')


@require_permission(Actions.SITE_SETTINGS_MANAGE)
def site_settings_view(request):
    """Espace d'édition des textes et coordonnées publiques du portail."""
    instance = SiteSettings.get_solo()
    if request.method == 'POST':
        old_snapshot = {
            f: getattr(instance, f)
            for f in SiteSettingsForm.Meta.fields
        }
        form = SiteSettingsForm(request.POST, instance=instance)
        if form.is_valid():
            saved = form.save()
            for field_name in SiteSettingsForm.Meta.fields:
                old_val = old_snapshot.get(field_name)
                new_val = getattr(saved, field_name)
                if old_val != new_val:
                    PortalAuditLog.objects.create(
                        category=PortalAuditLog.Category.SETTING_CHANGE,
                        target_label=f"SiteSettings.{field_name}",
                        old_value=str(old_val),
                        new_value=str(new_val),
                        changed_by=request.user,
                    )
            messages.success(request, "Les informations et paramètres ont été mis à jour.")
            return redirect('terminal:site_settings')
        messages.error(request, "Veuillez corriger les champs signalés ci-dessous.")
    else:
        form = SiteSettingsForm(instance=instance)

    slides = list(HeroSlide.objects.all())
    active_slides_count = sum(1 for s in slides if s.is_active)
    motifs_unavailable = list(Motif.objects.filter(type=Motif.Type.UNAVAILABLE))
    motifs_not_verified = list(Motif.objects.filter(type=Motif.Type.NOT_VERIFIED))
    settings_logs = list(
        PortalAuditLog.objects.select_related('changed_by')
        .filter(category=PortalAuditLog.Category.SETTING_CHANGE)[:15]
    )

    return render(request, 'portal/site_settings.html', {
        'form': form,
        'site_obj': instance,
        'slides': slides,
        'active_slides_count': active_slides_count,
        'slide_form': HeroSlideForm(initial={'display_order': len(slides) + 1}),
        'motifs_unavailable': motifs_unavailable,
        'motifs_not_verified': motifs_not_verified,
        'motif_types': Motif.Type.choices,
        'settings_logs': settings_logs,
    })


@require_permission(Actions.MOTIF_MANAGE)
@require_POST
def motif_create_view(request):
    """Ajoute un nouveau motif prédéfini (Superadmin uniquement)."""
    motif_type = request.POST.get('type', '').strip()
    label = request.POST.get('label', '').strip()[:160]
    raw_order = request.POST.get('display_order', '10').strip()
    requires_note = request.POST.get('requires_note') == 'on'

    if motif_type not in dict(Motif.Type.choices):
        messages.error(request, "Type de motif invalide.")
        return redirect('terminal:site_settings')
    if not label:
        messages.error(request, "Le libellé du motif est obligatoire.")
        return redirect('terminal:site_settings')
    if Motif.objects.filter(type=motif_type, label__iexact=label).exists():
        messages.warning(request, f"Le motif « {label} » existe déjà pour cette catégorie.")
        return redirect('terminal:site_settings')

    order = int(raw_order) if raw_order.isdigit() else 10
    motif = Motif.objects.create(
        type=motif_type,
        label=label,
        display_order=order,
        requires_note=requires_note,
        is_active=True,
    )
    PortalAuditLog.objects.create(
        category=PortalAuditLog.Category.SETTING_CHANGE,
        target_label=f"Motif:{motif.type}",
        old_value="",
        new_value=f"{motif.label} (ordre={motif.display_order}, precision={motif.requires_note})",
        changed_by=request.user,
    )
    messages.success(request, f"Motif « {motif.label} » ajouté.")
    return redirect('terminal:site_settings')


@require_permission(Actions.MOTIF_MANAGE)
@require_POST
def motif_toggle_view(request, pk):
    """Active ou désactive un motif (jamais de suppression)."""
    motif = get_object_or_404(Motif, pk=pk)
    if motif.is_system:
        messages.error(request, f"Le motif système « {motif.label} » ne peut pas être modifié.")
        return redirect('terminal:site_settings')

    old_state = "actif" if motif.is_active else "inactif"
    motif.is_active = not motif.is_active
    motif.save(update_fields=['is_active', 'updated_at'])
    new_state = "actif" if motif.is_active else "inactif"
    PortalAuditLog.objects.create(
        category=PortalAuditLog.Category.SETTING_CHANGE,
        target_label=f"Motif:{motif.pk}:{motif.label}",
        old_value=old_state,
        new_value=new_state,
        changed_by=request.user,
    )
    messages.info(request, f"Motif « {motif.label} » marqué {new_state}.")
    return redirect('terminal:site_settings')


@require_permission(Actions.MOTIF_MANAGE)
@require_POST
def motif_update_view(request, pk):
    """Met à jour l'ordre ou la précision obligatoire d'un motif."""
    motif = get_object_or_404(Motif, pk=pk)
    if motif.is_system:
        messages.error(request, f"Le motif système « {motif.label} » ne peut pas être modifié.")
        return redirect('terminal:site_settings')

    raw_order = request.POST.get('display_order', str(motif.display_order)).strip()
    if raw_order.isdigit():
        old_order = motif.display_order
        motif.display_order = int(raw_order)
        motif.save(update_fields=['display_order', 'updated_at'])
        if old_order != motif.display_order:
            PortalAuditLog.objects.create(
                category=PortalAuditLog.Category.SETTING_CHANGE,
                target_label=f"Motif:{motif.pk}:ordre",
                old_value=str(old_order),
                new_value=str(motif.display_order),
                changed_by=request.user,
            )
            messages.success(request, f"Ordre du motif « {motif.label} » mis à jour.")
    return redirect('terminal:site_settings')


@require_permission(Actions.SITE_SETTINGS_MANAGE)
@require_POST
def hero_slide_create_view(request):
    """Ajoute une image au carrousel d'arrière-plan de l'accueil."""
    form = HeroSlideForm(request.POST, request.FILES)
    if form.is_valid():
        slide = form.save()
        messages.success(request, f"Image « {slide} » ajoutée au carrousel.")
    else:
        messages.error(request, "Impossible d'ajouter l'image. Vérifiez le format du fichier.")
    return redirect('terminal:site_settings')


@require_permission(Actions.SITE_SETTINGS_MANAGE)
@require_POST
def hero_slide_toggle_view(request, pk):
    """Active ou désactive une image du carrousel."""
    slide = get_object_or_404(HeroSlide, pk=pk)
    slide.is_active = not slide.is_active
    slide.save(update_fields=['is_active'])
    state = "activée" if slide.is_active else "masquée"
    messages.info(request, f"Image « {slide} » {state}.")
    return redirect('terminal:site_settings')


@require_permission(Actions.SITE_SETTINGS_MANAGE)
@require_POST
def hero_slide_delete_view(request, pk):
    """Supprime une image du carrousel."""
    slide = get_object_or_404(HeroSlide, pk=pk)
    label = str(slide)
    if slide.image:
        slide.image.delete(save=False)
    slide.delete()
    messages.info(request, f"Image « {label} » supprimée du carrousel.")
    return redirect('terminal:site_settings')


