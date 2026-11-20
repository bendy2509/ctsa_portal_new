"""Suivi des conteneurs non vérifiés (reprise ou rejet)."""

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


@require_permission(Actions.CONTAINER_CARRYOVER)
def carryover_view(request):
    """Suivi de tous les conteneurs programmés mais pas encore vérifiés, et décision de reprise."""
    today = timezone.localdate()
    target_date = max(_parse_date(request.GET.get('date', ''), default_carry_target(request.user)), today)
    list_started = Container.objects.filter(scheduled_date=target_date).exists()

    status_filter = request.GET.get('status', '').strip()
    line_filter = request.GET.get('line', '').strip()

    base_qs = unverified_backlog(target_date)
    breakdown = _backlog_breakdown(base_qs)

    rows = base_qs
    if status_filter in dict(Container.Status.choices):
        rows = rows.filter(status=status_filter)
    if line_filter.isdigit():
        rows = rows.filter(shipping_line_id=int(line_filter))
    rows = rows.order_by('first_scheduled_date', 'scheduled_date', 'container_number')
    oldest = rows.first()
    page, page_query = _paginate(request, rows)
    rows = list(page.object_list)

    # Nombre de séances déjà passées pour chaque conteneur (longueur de la chaîne de reprises).
    session_counts = {
        row['container_number']: row['n']
        for row in Container.objects.filter(
            container_number__in=[c.container_number for c in rows],
            scheduled_date__lte=today,
        ).values('container_number').annotate(n=Count('id'))
    }
    for c in rows:
        c.session_count = session_counts.get(c.container_number, 1)

    return render(request, 'portal/carryover.html', {
        'today': today,
        'target_date': target_date,
        'list_started': list_started,
        'rows': rows,
        'breakdown': breakdown,
        'status_filter': status_filter,
        'line_filter': line_filter,
        'shipping_lines': ShippingLine.objects.filter(is_active=True),
        'status_choices': Container.Status.choices,
        'oldest': oldest,
        'page': page,
        'page_query': page_query,
    })


@require_permission(Actions.CONTAINER_CARRYOVER)
@require_POST
def carryover_decide_view(request):
    """Reprend (bouton en un clic ou sélection) ou rejette des conteneurs non vérifiés."""
    today = timezone.localdate()
    decision = request.POST.get('decision', '').strip()
    target_date = _parse_date(request.POST.get('date', ''), default_work_date(request.user))
    fallback = f"{reverse('terminal:carryover')}?date={target_date.isoformat()}"
    back = _safe_next(request, fallback)

    ids = [i for i in request.POST.getlist('ids') if i.isdigit()]
    if not ids:
        messages.warning(request, "Aucun conteneur sélectionné.")
        return redirect(back)
    if decision not in ('carry', 'reject'):
        messages.error(request, "Décision inconnue.")
        return redirect(back)
    if decision == 'carry' and target_date < today:
        messages.error(request, "Impossible de reprendre un conteneur dans une liste passée.")
        return redirect(back)

    # On ne décide que pour des conteneurs encore dans le suivi (ni vérifiés, ni déjà traités).
    candidates = (
        Container.objects.filter(pk__in=ids, carry_decision='')
        .exclude(verification_result=Container.Verification.VERIFIED)
        .select_related('shipping_line', 'prefix_ref')
    )
    done = []
    for old in candidates:
        if decision == 'carry':
            if old.scheduled_date >= target_date:
                continue
            _carry_into(old, target_date, request.user)
        else:
            if old.scheduled_date > today:
                continue
            _close_tracking(old, Container.CarryDecision.REJECTED, request.user, "Rejeté du suivi des non vérifiés")
        done.append(old.container_number)

    if not done:
        messages.warning(request, "Aucun conteneur à traiter : ils ont peut-être déjà été repris ou rejetés.")
    elif decision == 'carry':
        names = done[0] if len(done) == 1 else f"{len(done)} conteneurs"
        messages.success(
            request,
            f"{names} ajouté(s) à la liste du {target_date.strftime('%d/%m/%Y')} (statut « En attente »), sans nouvelle saisie."
        )
    else:
        names = done[0] if len(done) == 1 else f"{len(done)} conteneurs"
        messages.info(request, f"{names} rejeté(s) : ils ne seront plus proposés.")
    return redirect(back)


