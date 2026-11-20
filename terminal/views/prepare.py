"""Préparation des listes de vérification et recherche des conteneurs enregistrés."""

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


@require_permission(Actions.CONTAINER_PREPARE)
def registered_search_json_view(request):
    """Recherche prédictive JSON des conteneurs Enregistrés (hors liste de la date cible)."""
    raw_q = request.GET.get('q', '').strip()
    clean_q = normalize_container_number(raw_q)
    if len(clean_q) < 2:
        return JsonResponse({'results': []})

    today = timezone.localdate()
    target_date = _parse_date(request.GET.get('date', ''), today)
    already_scheduled_numbers = Container.objects.filter(
        scheduled_date=target_date
    ).values_list('normalized_number', flat=True)

    matches = (
        Container.registered_objects.select_related('shipping_line')
        .filter(normalized_number__icontains=clean_q)
        .exclude(normalized_number__in=already_scheduled_numbers)
        .order_by('normalized_number')[:10]
    )
    results = [
        {
            'id': c.pk,
            'number': c.normalized_number,
            'shipping_line': c.shipping_line.name if c.shipping_line else '',
            'arrived_at': timezone.localtime(c.arrived_at).strftime('%d/%m/%Y %H:%M') if c.arrived_at else '',
        }
        for c in matches
    ]
    return JsonResponse({'results': results})


@require_permission(Actions.CONTAINER_PREPARE)
@require_POST
def registered_add_to_list_view(request):
    """Ajoute un ou plusieurs conteneurs Enregistrés à la liste d'une date (statut En attente)."""
    today = timezone.localdate()
    target_date = _parse_date(request.POST.get('scheduled_date', ''), today)
    ids = [int(i) for i in request.POST.getlist('ids') if i.isdigit()]
    fallback = f"{reverse('terminal:prepare')}?date={target_date.isoformat()}"

    if not ids:
        messages.warning(request, "Veuillez sélectionner au moins un conteneur enregistré.")
        return redirect(_safe_next(request, fallback))

    added = 0
    skipped = 0
    for c in Container.registered_objects.filter(pk__in=ids):
        if Container.objects.filter(normalized_number=c.normalized_number, scheduled_date=target_date).exists():
            skipped += 1
            continue
        c.schedule_from_registered(target_date, acting_user=request.user)
        added += 1

    if added:
        messages.success(
            request,
            f"{added} conteneur(s) enregistré(s) ajouté(s) à la liste du {target_date.strftime('%d/%m/%Y')} (En attente).",
        )
    if skipped:
        messages.warning(request, f"{skipped} conteneur(s) déjà présent(s) sur la liste de cette date.")
    return redirect(_safe_next(request, fallback))


@require_permission(Actions.CONTAINER_PREPARE)
def prepare_view(request):
    """Préparer une liste : inscrire des conteneurs et reprendre les non vérifiés, à l'écart du
    tableau de bord, qui ne montre plus que la liste elle-même."""
    today = timezone.localdate()
    tomorrow = today + datetime.timedelta(days=1)
    selected_date = max(_parse_date(request.GET.get('date', ''), default_work_date(request.user)), today)

    session_qs = Container.objects.filter(scheduled_date=selected_date).select_related('shipping_line')
    registered_count = session_qs.count()
    backlog_qs = unverified_backlog(selected_date)
    draft = request.session.pop('_prepare_form_draft', None) or {}

    return render(request, 'portal/prepare.html', {
        'today': today,
        'tomorrow': tomorrow,
        'selected_date': selected_date,
        'registered_count': registered_count,
        # Les derniers inscrits, pour voir le résultat de ce qu'on vient d'ajouter.
        'recent': list(session_qs.order_by('-created_at', '-pk')[:8]),
        'backlog': _backlog_breakdown(backlog_qs),
        'backlog_rows': list(backlog_qs.order_by('first_scheduled_date', 'container_number')[:40]),
        'shipping_lines': ShippingLine.objects.filter(is_active=True),
        'status_choices': Container.Status.choices,
        'active_tab': draft.get('active_tab', 'single'),
        'single_number_value': draft.get('single_number', ''),
        'single_line_value': draft.get('single_line', ''),
        'confirm_verified': draft.get('confirm_verified'),
        'bulk_list_value': draft.get('bulk_list', ''),
        'bulk_line_value': draft.get('bulk_line', ''),
        'confirm_verified_bulk': draft.get('confirm_verified_bulk'),
    })




@require_permission(Actions.CONTAINER_PREPARE)
@require_POST
def container_add_single_view(request):
    today = timezone.localdate()
    default_date = today

    raw_number = request.POST.get('container_number', '')
    normalized = normalize_container_number(raw_number)
    target_date = _parse_date(request.POST.get('scheduled_date', ''), default_date)

    # Tout nouvel ajout dans une liste démarre « En attente » jusqu'à confirmation sur la rampe.
    status = Container.Status.PENDING

    shipping_line_id = request.POST.get('shipping_line') or ''
    note = request.POST.get('unavailability_note', '').strip()

    if not CONTAINER_NUMBER_REGEX.match(normalized):
        request.session['_prepare_form_draft'] = {
            'active_tab': 'single',
            'single_number': raw_number,
            'single_line': shipping_line_id,
        }
        messages.error(
            request,
            f"Numéro « {raw_number} » invalide : il doit comporter 4 lettres et 7 chiffres (ex. CMAU4052450)."
        )
        return redirect(_safe_next(request, _dashboard_url(target_date)))

    line_id = int(shipping_line_id) if shipping_line_id.isdigit() else None

    already_verified = (
        Container.objects.filter(
            container_number=normalized,
            verification_result=Container.Verification.VERIFIED,
        )
        .order_by('-verified_at', '-scheduled_date')
        .first()
    )
    if already_verified and request.POST.get('confirm_already_verified') != '1':
        verif_dt = already_verified.verified_at.date() if already_verified.verified_at else already_verified.scheduled_date
        verif_date_str = verif_dt.strftime('%d/%m/%Y')
        request.session['_prepare_form_draft'] = {
            'active_tab': 'single',
            'single_number': raw_number,
            'single_line': shipping_line_id,
            'confirm_verified': {
                'number': normalized,
                'verified_date': verif_date_str,
            },
        }
        messages.warning(
            request,
            f"Le conteneur {normalized} a déjà été vérifié le {verif_date_str}. Confirmez ci-dessous pour l'inscrire à nouveau."
        )
        return redirect(_safe_next(request, _dashboard_url(target_date)))

    existing = Container.objects.filter(
        container_number=normalized, scheduled_date=target_date
    ).first()

    # Garde-fou : un numéro déjà programmé et non vérifié n'est pas ré-enregistré, il est repris.
    prior = None
    if not existing:
        prior = (
            unverified_backlog(target_date)
            .filter(container_number=normalized)
            .order_by('-scheduled_date')
            .first()
        )
    if prior:
        carried = _carry_into(prior, target_date, request.user)
        messages.info(
            request,
            f"{normalized} était déjà programmé depuis le "
            f"{carried.first_scheduled_date.strftime('%d/%m/%Y')} : il a été repris dans la liste "
            f"avec le statut « En attente » (ligne maritime conservée)."
        )
        return redirect(_safe_next(request, _dashboard_url(target_date)))

    if existing:
        existing.status = status
        if line_id:
            existing.shipping_line_id = line_id
        existing.unavailability_note = note
        existing.save(acting_user=request.user)
        messages.info(
            request,
            f"Le conteneur existant {normalized} a été mis à jour ({existing.get_status_display()})."
        )
    else:
        registered = Container.registered_objects.filter(normalized_number=normalized).first()
        if registered:
            registered.schedule_from_registered(
                target_date,
                acting_user=request.user,
                shipping_line_id=line_id,
            )
            messages.success(
                request,
                f"Conteneur enregistré {normalized} ajouté à la liste du {target_date.strftime('%d/%m/%Y')} (En attente).",
            )
            return redirect(_safe_next(request, _dashboard_url(target_date)))
        container = Container(
            container_number=normalized,
            scheduled_date=target_date,
            status=status,
            shipping_line_id=line_id,
            unavailability_note=note,
        )
        container.save(acting_user=request.user)
        line_info = f" ({container.shipping_line.name})" if container.shipping_line else ""
        messages.success(
            request,
            f"Conteneur {normalized}{line_info} ajouté avec le statut « {container.get_status_display()} »."
        )

    return redirect(_safe_next(request, _dashboard_url(target_date)))


@require_permission(Actions.CONTAINER_PREPARE)
@require_POST
def container_add_bulk_view(request):
    today = timezone.localdate()
    default_date = today

    raw_list = request.POST.get('container_list', '')
    target_date = _parse_date(request.POST.get('scheduled_date', ''), default_date)
    shipping_line_id = request.POST.get('shipping_line') or ''

    # Tout nouvel ajout dans une liste démarre « En attente » jusqu'à confirmation sur la rampe.
    default_status = Container.Status.PENDING
    resolved_line_id = int(shipping_line_id) if shipping_line_id.isdigit() else None

    tokens = [t.strip() for t in re.split(r'[\r\n,;]+', raw_list) if t.strip()]
    if not tokens:
        request.session['_prepare_form_draft'] = {
            'active_tab': 'bulk',
            'bulk_list': raw_list,
            'bulk_line': shipping_line_id,
        }
        messages.warning(request, "Aucun numéro de conteneur fourni dans la zone d'ajout massif.")
        return redirect(_safe_next(request, _dashboard_url(target_date)))

    if request.POST.get('confirm_already_verified') != '1':
        valid_norms = [normalize_container_number(t) for t in tokens if CONTAINER_NUMBER_REGEX.match(normalize_container_number(t))]
        verified_map = {}
        for vc in (
            Container.objects.filter(
                container_number__in=valid_norms,
                verification_result=Container.Verification.VERIFIED,
            )
            .order_by('-verified_at', '-scheduled_date')
        ):
            if vc.container_number not in verified_map:
                vdate = vc.verified_at.date() if vc.verified_at else vc.scheduled_date
                verified_map[vc.container_number] = vdate.strftime('%d/%m/%Y')
        if verified_map:
            details = ', '.join(f"{num} (vérifié le {dt})" for num, dt in verified_map.items())
            request.session['_prepare_form_draft'] = {
                'active_tab': 'bulk',
                'bulk_list': raw_list,
                'bulk_line': shipping_line_id,
                'confirm_verified_bulk': details,
            }
            messages.warning(
                request,
                f"Conteneur(s) déjà vérifié(s) détecté(s) : {details}. Confirmez ci-dessous pour poursuivre l'enregistrement."
            )
            return redirect(_safe_next(request, _dashboard_url(target_date)))

    created_count = 0
    skipped_duplicates = []
    invalid_tokens = []
    carried_numbers = []

    for token in tokens:
        normalized = normalize_container_number(token)
        if not CONTAINER_NUMBER_REGEX.match(normalized):
            invalid_tokens.append(token)
            continue

        if Container.objects.filter(container_number=normalized, scheduled_date=target_date).exists():
            skipped_duplicates.append(normalized)
            continue

        # Garde-fou : déjà programmé et non vérifié → reprise, pas de nouvelle fiche.
        prior = (
            unverified_backlog(target_date)
            .filter(container_number=normalized)
            .order_by('-scheduled_date')
            .first()
        )
        if prior:
            _carry_into(prior, target_date, request.user)
            carried_numbers.append(normalized)
            continue

        registered = Container.registered_objects.filter(normalized_number=normalized).first()
        if registered:
            registered.schedule_from_registered(
                target_date,
                acting_user=request.user,
                shipping_line_id=resolved_line_id,
            )
            created_count += 1
            continue

        c = Container(
            container_number=normalized,
            scheduled_date=target_date,
            status=default_status,
            shipping_line_id=resolved_line_id,
        )
        c.save(acting_user=request.user)
        created_count += 1

    if created_count:
        messages.success(
            request,
            f"Ajout massif terminé : {created_count} conteneur(s) programmé(s) pour le {target_date.strftime('%d/%m/%Y')}."
        )
    if carried_numbers:
        messages.info(
            request,
            f"{len(carried_numbers)} conteneur(s) déjà programmé(s) et non vérifié(s), repris sans "
            f"nouvelle saisie : {', '.join(carried_numbers[:8])}."
        )
    if skipped_duplicates:
        messages.info(
            request,
            f"{len(skipped_duplicates)} doublon(s) ignoré(s) : {', '.join(skipped_duplicates[:8])}."
        )
    if invalid_tokens:
        messages.error(
            request,
            f"{len(invalid_tokens)} numéro(s) rejeté(s) (format 4 lettres + 7 chiffres requis) : {', '.join(invalid_tokens[:6])}."
        )

    return redirect(_safe_next(request, _dashboard_url(target_date)))


