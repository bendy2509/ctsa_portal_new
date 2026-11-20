"""Tableau de bord d'exploitation RAMPE."""

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


@require_permission(Actions.PORTAL_DASHBOARD)
def dashboard_view(request):
    """Tableau de bord : résumé de la séance, indicateurs, points d'attention et activité récente."""
    today = timezone.localdate()
    selected_date = _parse_date(request.GET.get('date', ''), today)
    day_qs = Container.objects.filter(scheduled_date=selected_date)

    S, V = Container.Status, Container.Verification
    stats = {
        'total': day_qs.count(),
        'confirmed': day_qs.filter(status=S.CONFIRMED).count(),
        'pending': day_qs.filter(status=S.PENDING).count(),
        'unavailable': day_qs.filter(status=S.UNAVAILABLE).count(),
        'verified': day_qs.filter(verification_result=V.VERIFIED).count(),
        'not_verified': day_qs.filter(verification_result=V.NOT_VERIFIED).count(),
        'to_verify': day_qs.filter(status=S.CONFIRMED, verification_result='').count(),
    }
    stats['verified_rate'] = _rate(stats['verified'], stats['confirmed'])
    show_verification = selected_date <= today

    # Répartition par ligne maritime (les conteneurs sans ligne sont regroupés).
    by_line = list(
        day_qs.values('shipping_line__name')
        .annotate(
            total=Count('id'),
            confirmed=Count('id', filter=Q(status=S.CONFIRMED)),
            pending=Count('id', filter=Q(status=S.PENDING)),
            unavailable=Count('id', filter=Q(status=S.UNAVAILABLE)),
        )
        .order_by('-total', 'shipping_line__name')
    )
    for row in by_line:
        row['name'] = row['shipping_line__name'] or 'Ligne non renseignée'
        row['confirmed_pct'] = _rate(row['confirmed'], row['total']) or 0
        row['pending_pct'] = _rate(row['pending'], row['total']) or 0
        row['unavailable_pct'] = 100 - row['confirmed_pct'] - row['pending_pct'] if row['total'] else 0

    # Sept derniers jours jusqu'à la date affichée : programmés et vérifiés.
    first_day = selected_date - datetime.timedelta(days=6)
    week_rows = {
        r['scheduled_date']: r
        for r in Container.objects.filter(scheduled_date__range=(first_day, selected_date))
        .values('scheduled_date')
        .annotate(total=Count('id'), verified=Count('id', filter=Q(verification_result=V.VERIFIED)))
    }
    week = []
    for offset in range(7):
        day = first_day + datetime.timedelta(days=offset)
        row = week_rows.get(day, {})
        week.append({'date': day, 'total': row.get('total', 0), 'verified': row.get('verified', 0)})
    peak = max((d['total'] for d in week), default=0)
    for d in week:
        d['height'] = round(100 * d['total'] / peak) if peak else 0
        d['verified_height'] = round(100 * d['verified'] / peak) if peak else 0

    backlog = _backlog_breakdown(unverified_backlog(default_carry_target(request.user)))
    recent_logs = (
        ContainerStatusLog.objects.select_related('container', 'changed_by')
        .filter(container__scheduled_date=selected_date)
        .order_by('-changed_at')[:8]
    )

    return render(request, 'portal/dashboard.html', {
        'today': today,
        'tomorrow': today + datetime.timedelta(days=1),
        'selected_date': selected_date,
        'previous_date': selected_date - datetime.timedelta(days=1),
        'next_date': selected_date + datetime.timedelta(days=1),
        'show_verification': show_verification,
        'stats': stats,
        'by_line': by_line,
        'week': week,
        'backlog': backlog,
        'recent_logs': recent_logs,
    })


