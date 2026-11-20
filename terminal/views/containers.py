"""Liste, détail, modification, transitions de statut, archivage et restauration des conteneurs."""

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


@require_permission(Actions.CONTAINER_LIST)
def container_list_view(request):
    today = timezone.localdate()
    tomorrow = today + datetime.timedelta(days=1)

    default_date = today
    selected_date = _parse_date(request.GET.get('date', ''), default_date)
    status_filter = request.GET.get('status', '').strip()
    line_filter = request.GET.get('line', '').strip()
    search_q = request.GET.get('q', '').strip()
    verification_filter = request.GET.get('verification', '').strip()

    base_qs = Container.objects.filter(scheduled_date=selected_date).select_related(
        'shipping_line', 'prefix_ref', 'created_by', 'updated_by',
        'carried_from', 'verified_by',
    )

    stats = {
        'total': base_qs.count(),
        'confirmed': base_qs.filter(status=Container.Status.CONFIRMED).count(),
        'pending': base_qs.filter(status=Container.Status.PENDING).count(),
        'unavailable': base_qs.filter(status=Container.Status.UNAVAILABLE).count(),
        'verified': base_qs.filter(verification_result=Container.Verification.VERIFIED).count(),
        'not_verified': base_qs.filter(verification_result=Container.Verification.NOT_VERIFIED).count(),
        'to_verify': base_qs.filter(status=Container.Status.CONFIRMED, verification_result='').count(),
    }
    # La vérification ne se marque que pour une séance commencée (aujourd'hui ou passée).
    show_verification = selected_date <= today

    containers = base_qs
    if verification_filter == 'to_verify':
        containers = containers.filter(status=Container.Status.CONFIRMED, verification_result='')
    elif verification_filter in Container.Verification.values:
        containers = containers.filter(verification_result=verification_filter)
    if status_filter in dict(Container.Status.choices):
        containers = containers.filter(status=status_filter)
    if line_filter.isdigit():
        containers = containers.filter(shipping_line_id=int(line_filter))
    if search_q:
        clean_q = normalize_container_number(search_q)
        containers = containers.filter(
            Q(container_number__icontains=clean_q)
            | Q(shipping_line__name__icontains=search_q)
            | Q(unavailability_note__icontains=search_q)
        )

    page, page_query = _paginate(request, containers)

    shipping_lines = ShippingLine.objects.filter(is_active=True)
    recent_logs = (
        ContainerStatusLog.objects.select_related('container', 'changed_by')
        .filter(container__scheduled_date=selected_date)
        .order_by('-changed_at')[:10]
    )

    return render(request, 'portal/containers.html', {
        'verification_filter': verification_filter,
        'show_verification': show_verification,
        'filtered': bool(status_filter or line_filter or search_q or verification_filter),
        'today': today,
        'tomorrow': tomorrow,
        'selected_date': selected_date,
        'status_filter': status_filter,
        'line_filter': line_filter,
        'search_q': search_q,
        'stats': stats,
        'containers': page,
        'page': page,
        'page_query': page_query,
        'shipping_lines': shipping_lines,
        'recent_logs': recent_logs,
        'status_choices': Container.Status.choices,
    })




@require_permission(Actions.CONTAINER_CHANGE_STATUS)
@require_POST
def container_update_status_view(request, pk):
    container = get_object_or_404(Container.all_objects, pk=pk)
    new_status = request.POST.get('status', container.status)
    note = request.POST.get('unavailability_note', '').strip()
    raw_reason = request.POST.get('unavailability_reason', '').strip()
    raw_version = request.POST.get('version', '').strip()
    expected_version = int(raw_version) if raw_version.isdigit() else None
    shipping_line_id = request.POST.get('shipping_line') or None

    if new_status in dict(Container.Status.choices):
        if shipping_line_id and shipping_line_id.isdigit():
            container.shipping_line_id = int(shipping_line_id)
        try:
            reason_obj = None
            clean_note = ''
            if new_status == Container.Status.UNAVAILABLE:
                reason_obj, clean_note = _resolve_motif(Motif.Type.UNAVAILABLE, raw_reason, note)
            container.change_status(
                new_status,
                acting_user=request.user,
                reason=reason_obj,
                note=clean_note,
                expected_version=expected_version,
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            messages.success(
                request,
                f"Conteneur {container.container_number} mis à jour ({container.get_status_display()}).",
            )

    fallback = _dashboard_url(container.scheduled_date) if container.scheduled_date else reverse('terminal:containers')
    return redirect(_safe_next(request, fallback))


@require_permission(Actions.CONTAINER_CHANGE_STATUS)
@require_POST
def container_status_bulk_view(request):
    """Change le statut (Confirmé, En attente, Indisponible) des conteneurs cochés ou de tous les conteneurs en attente."""
    today = timezone.localdate()
    target_date = _parse_date(request.POST.get('date', ''), today)
    new_status = request.POST.get('status', '').strip()
    note = request.POST.get('unavailability_note', '').strip()
    raw_reason = request.POST.get('unavailability_reason', '').strip()
    fallback = _dashboard_url(target_date)

    if new_status not in dict(Container.Status.choices):
        messages.error(request, "Statut inconnu.")
        return redirect(_safe_next(request, fallback))

    reason_obj = None
    clean_note = ''
    if new_status == Container.Status.UNAVAILABLE:
        try:
            reason_obj, clean_note = _resolve_motif(Motif.Type.UNAVAILABLE, raw_reason, note)
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
            return redirect(_safe_next(request, fallback))

    qs = Container.objects.filter(scheduled_date=target_date)
    ids = [i for i in request.POST.getlist('ids') if i.isdigit()]
    if request.POST.get('scope') == 'all_pending':
        qs = qs.filter(status=Container.Status.PENDING)
    elif ids:
        qs = qs.filter(pk__in=ids)
    else:
        messages.warning(request, "Veuillez cocher au moins un conteneur dans la liste.")
        return redirect(_safe_next(request, fallback))

    updated_count = 0
    skipped_locked = 0
    for container in qs:
        try:
            container.change_status(
                new_status,
                acting_user=request.user,
                reason=reason_obj,
                note=clean_note,
            )
            updated_count += 1
        except ValidationError:
            skipped_locked += 1

    label = dict(Container.Status.choices)[new_status]
    if updated_count:
        messages.success(
            request,
            f"{updated_count} conteneur(s) passé(s) au statut « {label} » pour la séance du {target_date.strftime('%d/%m/Y').replace('/Y', '/' + target_date.strftime('%Y'))}.",
        )
    if skipped_locked:
        messages.warning(
            request,
            f"{skipped_locked} conteneur(s) ignoré(s) car déjà inspecté(s) ou verrouillé(s).",
        )
    if not updated_count and not skipped_locked:
        messages.warning(request, "Aucun conteneur à modifier pour cette sélection.")
    return redirect(_safe_next(request, fallback))


@require_permission(Actions.CONTAINER_ARCHIVE)
@require_POST
def container_delete_view(request, pk):
    container = get_object_or_404(Container.all_objects, pk=pk)
    if not peut(request.user, Actions.CONTAINER_ARCHIVE, container):
        return render_403(request, "La fenêtre de suppression est dépassée pour votre rôle.")
    scheduled = container.scheduled_date
    num = container.container_number
    reason = request.POST.get('archive_reason', '').strip()
    try:
        if container.status == Container.Status.REGISTERED:
            container.archive(acting_user=request.user, reason=reason or "Archivage depuis la fiche")
            messages.info(request, f"Le conteneur {num} a été archivé.")
        else:
            was_arrival = container.origin == Container.Origin.ARRIVE_SANS_LISTE
            container.remove_from_list(acting_user=request.user, reason=reason or "Retiré de la liste")
            if was_arrival:
                messages.info(request, f"Le conteneur {num} a été retiré de la liste et remis à l'état Enregistré.")
            else:
                messages.info(request, f"Le conteneur {num} a été retiré de la liste du {scheduled.strftime('%d/%m/%Y')} et archivé.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect('terminal:container_detail', pk=container.pk)

    if scheduled:
        return redirect(f"{reverse('terminal:containers')}?date={scheduled.isoformat()}")
    return redirect('terminal:containers')


@require_permission(Actions.CONTAINER_DETAIL)
def container_detail_view(request, pk):
    """Fiche détail d'un conteneur programmé : infos, changement de statut, suppression, historique."""
    container = get_object_or_404(
        Container.all_objects.select_related(
            'shipping_line', 'prefix_ref', 'created_by', 'updated_by', 'verified_by', 'carried_from'
        ),
        pk=pk,
    )
    logs = (
        ContainerStatusLog.objects.select_related('changed_by')
        .filter(container=container)
        .order_by('-changed_at')
    )
    return render(request, 'portal/container_detail.html', {
        'container': container,
        'logs': logs,
        'shipping_lines': ShippingLine.objects.filter(is_active=True),
    })


@require_permission(Actions.CONTAINER_CHANGE_STATUS)
def container_edit_view(request, pk):
    """Page dédiée de modification d'un conteneur programmé."""
    container = get_object_or_404(Container, pk=pk)
    shipping_lines = ShippingLine.objects.filter(is_active=True)
    errors = {}
    form_values = {
        'container_number': container.container_number,
        'shipping_line': str(container.shipping_line_id or ''),
        'scheduled_date': container.scheduled_date.isoformat(),
        'status': container.status,
        'unavailability_note': container.unavailability_note,
    }

    if request.method == 'POST':
        raw_number = request.POST.get('container_number', '').strip()
        normalized = normalize_container_number(raw_number)
        raw_line = request.POST.get('shipping_line', '').strip()
        raw_date = request.POST.get('scheduled_date', '').strip()
        new_status = request.POST.get('status', '').strip()
        raw_reason = request.POST.get('unavailability_reason', '').strip()
        note = request.POST.get('unavailability_note', '').strip()

        form_values = {
            'container_number': raw_number,
            'shipping_line': raw_line,
            'scheduled_date': raw_date,
            'status': new_status,
            'unavailability_note': note,
        }

        if not CONTAINER_NUMBER_REGEX.match(normalized):
            errors['container_number'] = "Format requis : 4 lettres suivies de 7 chiffres (ex. CMAU4052450)."

        try:
            parsed_date = datetime.date.fromisoformat(raw_date)
        except ValueError:
            parsed_date = None
            errors['scheduled_date'] = "Veuillez saisir une date valide."

        if new_status not in dict(Container.Status.choices):
            errors['status'] = "Statut invalide."

        line_obj = None
        if raw_line:
            if raw_line.isdigit():
                line_obj = ShippingLine.objects.filter(pk=int(raw_line)).first()
            if not line_obj:
                errors['shipping_line'] = "Ligne maritime invalide."

        if (
            not errors
            and Container.objects.filter(container_number=normalized, scheduled_date=parsed_date)
            .exclude(pk=container.pk)
            .exists()
        ):
            errors['container_number'] = (
                f"Le conteneur {normalized} est déjà programmé le {parsed_date.strftime('%d/%m/%Y')}."
            )

        if not errors and (new_status != container.status or parsed_date != container.scheduled_date):
            ok, err = container.can_change_status(new_status)
            if not ok:
                errors['status'] = err

        reason_obj = None
        clean_note = ''
        if not errors and new_status == Container.Status.UNAVAILABLE:
            try:
                reason_obj, clean_note = _resolve_motif(Motif.Type.UNAVAILABLE, raw_reason, note)
            except ValidationError as exc:
                errors['unavailability_note'] = exc.messages[0]

        if not errors:
            container.container_number = normalized
            container.shipping_line = line_obj
            container.scheduled_date = parsed_date
            container.status = new_status
            container.unavailability_reason = reason_obj
            container.unavailability_note = clean_note
            container.version = (container.version or 1) + 1
            container.save(acting_user=request.user)
            messages.success(request, f"Conteneur {container.container_number} enregistré.")
            return redirect('terminal:container_detail', pk=container.pk)

    return render(request, 'portal/container_edit.html', {
        'container': container,
        'shipping_lines': shipping_lines,
        'status_choices': Container.Status.choices,
        'errors': errors,
        'form_values': form_values,
    })




@require_permission(Actions.CONTAINER_RESTORE)
@require_POST
def container_restore_view(request, pk):
    """Restaure un conteneur archivé (Superadmin uniquement)."""
    container = get_object_or_404(Container.all_objects, pk=pk)
    reason = request.POST.get('restore_reason', '').strip() or "Restauration depuis l'historique"
    try:
        container.restore(acting_user=request.user, reason=reason)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        messages.success(request, f"Conteneur {container.container_number} restauré.")
    return redirect('terminal:status_logs')


