"""Inspection de séance : marquage Vérifié / Non vérifié, correction et suppression de mouvement."""

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


def _archive_verification_movement(container, acting_user, archive_reason: str, expected_version):
    """Archive le mouvement de vérification actif et remet le conteneur à Non inspecté."""
    with transaction.atomic():
        locked = Container.all_objects.select_for_update().get(pk=container.pk)
        if expected_version is not None and locked.version != expected_version:
            raise ValidationError(
                "Conflit de concurrence : ce conteneur a été modifié entre-temps. Actualisez la page."
            )
        VerificationEvent.objects.filter(
            container=locked, archived_at__isnull=True
        ).update(
            archived_at=timezone.now(),
            archived_by=acting_user,
            archive_reason=archive_reason,
        )
        sync_container_verification(locked, acting_user=acting_user)
        ContainerStatusLog.objects.create(
            container=locked,
            action=ContainerStatusLog.Action.VERIFICATION_ARCHIVED,
            old_status=locked.status,
            new_status=locked.status,
            reason_text=archive_reason,
            changed_by=acting_user,
        )


def _correct_verification_movement(container, result: str, acting_user, reason_obj, clean_note: str, expected_version):
    """Corrige une inspection dans la fenêtre de temps autorisée."""
    with transaction.atomic():
        locked = Container.all_objects.select_for_update().get(pk=container.pk)
        if expected_version is not None and locked.version != expected_version:
            raise ValidationError(
                "Conflit de concurrence : ce conteneur a été modifié entre-temps. Actualisez la page."
            )
        now = timezone.now()
        VerificationEvent.objects.filter(
            container=locked, archived_at__isnull=True
        ).update(
            archived_at=now,
            archived_by=acting_user,
            archive_reason=(clean_note or "Correction dans la fenêtre") if result == '' else "Remplacé par correction",
        )
        if result != '':
            VerificationEvent.objects.create(
                container=locked,
                result=result,
                reason=reason_obj,
                note=clean_note,
                created_by=acting_user,
                created_at=now,
                corrected_by=acting_user,
                corrected_at=now,
            )
        sync_container_verification(locked, acting_user=acting_user)
        reason_label = (
            f"{reason_obj.label} — {clean_note}" if reason_obj and clean_note
            else (reason_obj.label if reason_obj else (clean_note or "Correction d'inspection"))
        )[:255]
        ContainerStatusLog.objects.create(
            container=locked,
            action=ContainerStatusLog.Action.VERIFICATION_CORRECTED,
            old_status=locked.status,
            new_status=locked.status,
            reason_text=reason_label,
            changed_by=acting_user,
        )


@require_permission(Actions.CONTAINER_VERIFY)
@require_POST
def container_set_verification_view(request, pk):
    """Marque un conteneur confirmé Vérifié / Non vérifié, corrige (< fenêtre) ou supprime le mouvement (> fenêtre)."""
    container = get_object_or_404(Container, pk=pk)
    result = request.POST.get('result', '').strip()
    note = request.POST.get('verification_note', '').strip()[:255]
    raw_reason = request.POST.get('verification_reason', '').strip()
    archive_reason = request.POST.get('archive_reason', '').strip()[:255]
    raw_version = request.POST.get('version', '').strip()
    expected_version = int(raw_version) if raw_version.isdigit() else None
    fallback = _dashboard_url(container.scheduled_date) if container.scheduled_date else reverse('terminal:containers')

    # Cas 1 : Le conteneur a déjà un résultat -> correction (< fenêtre) ou suppression du mouvement (> fenêtre)
    if container.verification_result:
        if container.carry_decision in (Container.CarryDecision.CARRIED, Container.CarryDecision.REJECTED):
            messages.error(
                request,
                f"{container.container_number} : impossible de corriger ou supprimer le mouvement d'un conteneur déjà repris ou rejeté.",
            )
            return redirect(_safe_next(request, fallback))

        is_delete_movement = request.POST.get('delete_movement') == '1' or (
            result == '' and not peut(request.user, Actions.CONTAINER_VERIFY_CORRECT, container)
        )
        if is_delete_movement:
            if not peut(request.user, Actions.CONTAINER_VERIFY_DELETE_MOVEMENT, container):
                return render_403(request, "Vous n'avez pas l'autorisation de supprimer ce mouvement de vérification.")
            if not archive_reason:
                messages.error(request, "Un motif est obligatoire pour supprimer le mouvement de vérification.")
                return redirect(_safe_next(request, fallback))
            try:
                _archive_verification_movement(container, request.user, archive_reason, expected_version)
            except ValidationError as exc:
                messages.error(request, f"{container.container_number} : {exc.messages[0]}")
            else:
                messages.success(request, f"Mouvement de vérification archivé pour {container.container_number} (remis à Non inspecté).")
            return redirect(_safe_next(request, fallback))

        if not peut(request.user, Actions.CONTAINER_VERIFY_CORRECT, container):
            return render_403(request, "La fenêtre de correction est dépassée ou ce marquage appartient à un autre inspecteur.")

        reason_obj, clean_note = None, ''
        if result == Container.Verification.NOT_VERIFIED:
            try:
                reason_obj, clean_note = _resolve_motif(Motif.Type.NOT_VERIFIED, raw_reason, note)
            except ValidationError as exc:
                messages.error(request, f"{container.container_number} : {exc.messages[0]}")
                return redirect(_safe_next(request, fallback))

        try:
            _correct_verification_movement(container, result, request.user, reason_obj, clean_note, expected_version)
        except ValidationError as exc:
            messages.error(request, f"{container.container_number} : {exc.messages[0]}")
        else:
            label = container.get_verification_result_display() if result else "remis à Non inspecté"
            messages.success(request, f"Conteneur {container.container_number} corrigé ({label}).")
        return redirect(_safe_next(request, fallback))

    # Cas 2 : Premier marquage sur un conteneur Non inspecté
    reason_obj, clean_note = None, ''
    if result == Container.Verification.NOT_VERIFIED:
        try:
            reason_obj, clean_note = _resolve_motif(Motif.Type.NOT_VERIFIED, raw_reason, note)
        except ValidationError as exc:
            messages.error(request, f"{container.container_number} : {exc.messages[0]}")
            return redirect(_safe_next(request, fallback))

    try:
        container.set_verification(
            result,
            acting_user=request.user,
            note=clean_note,
            reason=reason_obj,
            expected_version=expected_version,
        )
    except ValidationError as exc:
        messages.error(request, f"{container.container_number} : {exc.messages[0]}")
    else:
        label = container.get_verification_result_display() if result else "marquage annulé"
        messages.success(request, f"Conteneur {container.container_number} : {label}.")
    return redirect(_safe_next(request, fallback))


@require_permission(Actions.CONTAINER_VERIFY)
@require_POST
def container_verify_bulk_view(request):
    """Clôture de séance : seuls les conteneurs Confirmés et Non inspectés sont éligibles."""
    today = timezone.localdate()
    target_date = _parse_date(request.POST.get('date', ''), today)
    result = request.POST.get('result', '').strip()
    note = request.POST.get('verification_note', '').strip()
    raw_reason = request.POST.get('verification_reason', '').strip()
    fallback = _dashboard_url(target_date)

    if result not in Container.Verification.values:
        messages.error(request, "Résultat de vérification inconnu.")
        return redirect(_safe_next(request, fallback))

    reason_obj = None
    clean_note = ''
    if result == Container.Verification.NOT_VERIFIED:
        try:
            reason_obj, clean_note = _resolve_motif(Motif.Type.NOT_VERIFIED, raw_reason, note)
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
            return redirect(_safe_next(request, fallback))

    qs = Container.objects.filter(scheduled_date=target_date, status=Container.Status.CONFIRMED)
    ids = [i for i in request.POST.getlist('ids') if i.isdigit()]
    if request.POST.get('scope') == 'remaining':
        qs = qs.filter(verification_result='')
    elif ids:
        qs = qs.filter(pk__in=ids)
    else:
        messages.warning(request, "Aucun conteneur sélectionné.")
        return redirect(_safe_next(request, fallback))

    done = 0
    skipped = 0
    for container in qs:
        if container.verification_result != '':
            skipped += 1
            continue
        try:
            container.set_verification(
                result,
                acting_user=request.user,
                note=clean_note,
                reason=reason_obj,
                expected_version=container.version,
            )
            done += 1
        except ValidationError:
            skipped += 1

    label = dict(Container.Verification.choices)[result].lower()
    if done:
        messages.success(
            request,
            f"{done} conteneur(s) marqué(s) « {label} » pour la séance du {target_date.strftime('%d/%m/%Y')}."
        )
    if skipped:
        messages.warning(
            request,
            f"{skipped} conteneur(s) ignoré(s) (déjà inspectés ou non éligibles).",
        )
    if not done and not skipped:
        messages.warning(request, "Aucun conteneur confirmé à marquer pour cette séance.")
    return redirect(_safe_next(request, fallback))


@require_permission(Actions.CONTAINER_VERIFY)
def inspection_view(request):
    """Onglet Inspection : marquer Vérifié / Non vérifié les conteneurs confirmés d'une séance."""
    today = timezone.localdate()
    # On n'inspecte qu'une séance commencée : aujourd'hui ou une date passée.
    selected_date = min(_parse_date(request.GET.get('date', ''), today), today)
    search_q = request.GET.get('q', '').strip()
    state = request.GET.get('etat', '').strip()

    session_qs = Container.objects.filter(scheduled_date=selected_date)
    confirmed_qs = session_qs.filter(status=Container.Status.CONFIRMED).select_related(
        'shipping_line', 'verified_by', 'carried_from'
    )
    stats = {
        'scheduled': session_qs.count(),
        'confirmed': confirmed_qs.count(),
        'verified': confirmed_qs.filter(verification_result=Container.Verification.VERIFIED).count(),
        'not_verified': confirmed_qs.filter(verification_result=Container.Verification.NOT_VERIFIED).count(),
        'to_verify': confirmed_qs.filter(verification_result='').count(),
    }
    stats['not_confirmed'] = stats['scheduled'] - stats['confirmed']
    stats['rate'] = _rate(stats['verified'], stats['confirmed'])

    rows = confirmed_qs
    if state == 'to_verify':
        rows = rows.filter(verification_result='')
    elif state in Container.Verification.values:
        rows = rows.filter(verification_result=state)
    if search_q:
        rows = rows.filter(container_number__icontains=normalize_container_number(search_q))
    # Les conteneurs encore à marquer en premier.
    rows = sorted(rows, key=lambda c: (c.verification_result != '', c.container_number))
    page, page_query = _paginate(request, rows)
    not_confirmed_rows = list(
        session_qs.exclude(status=Container.Status.CONFIRMED)
        .select_related('shipping_line')
        .order_by('status', 'container_number')
    )

    return render(request, 'portal/inspection/session.html', {
        'today': today,
        'selected_date': selected_date,
        'previous_date': selected_date - datetime.timedelta(days=1),
        'next_date': selected_date + datetime.timedelta(days=1) if selected_date < today else None,
        'stats': stats,
        'rows': page,
        'page': page,
        'page_query': page_query,
        'not_confirmed_rows': not_confirmed_rows,
        'search_q': search_q,
        'state': state,
    })


