"""Tableau de bord d'exploitation RAMPE."""

import csv
import datetime
import hashlib
import math
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

from terminal.services.reports import _rate, _reason_breakdown
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


def _pct_delta(current: int, previous: int):
    """Variation en % arrondie par rapport à la période précédente (None si référence nulle)."""
    if not previous:
        return None
    return round(100 * (current - previous) / previous)


def _format_delta(delta):
    if delta is None:
        return '—'
    if delta > 0:
        return f'+{delta}%'
    return f'{delta}%'


@require_permission(Actions.PORTAL_DASHBOARD)
def dashboard_view(request):
    """Tableau de bord : résumé de la séance, indicateurs, graphiques, motifs et file prioritaire."""
    today = timezone.localdate()
    selected_date = _parse_date(request.GET.get('date', ''), today)
    raw_du = request.GET.get('du', '').strip()
    raw_au = request.GET.get('au', '').strip()

    if raw_du or raw_au:
        period_start = _parse_date(raw_du, selected_date)
        period_end = _parse_date(raw_au, period_start)
        if period_end < period_start:
            period_start, period_end = period_end, period_start
        period = 'custom'
        period_days = (period_end - period_start).days + 1
        selected_date = period_end
    else:
        period = request.GET.get('period', 'day').strip()
        if period not in ('day', '7d', '30d'):
            period = 'day'
        period_days = {'day': 1, '7d': 7, '30d': 30}[period]
        period_end = selected_date
        period_start = selected_date - datetime.timedelta(days=period_days - 1)

    period_qs = Container.objects.filter(scheduled_date__range=(period_start, period_end))

    prev_end = period_start - datetime.timedelta(days=1)
    prev_start = prev_end - datetime.timedelta(days=period_days - 1)
    prev_qs = Container.objects.filter(scheduled_date__range=(prev_start, prev_end))

    S, V = Container.Status, Container.Verification
    stat_agg = dict(
        total=Count('id'),
        confirmed=Count('id', filter=Q(status=S.CONFIRMED)),
        pending=Count('id', filter=Q(status=S.PENDING)),
        unavailable=Count('id', filter=Q(status=S.UNAVAILABLE)),
        verified=Count('id', filter=Q(verification_result=V.VERIFIED)),
        not_verified=Count('id', filter=Q(verification_result=V.NOT_VERIFIED)),
        to_verify=Count('id', filter=Q(status=S.CONFIRMED, verification_result='')),
    )
    stats = period_qs.aggregate(**stat_agg)
    prev_stats = prev_qs.aggregate(**stat_agg)

    stats['registered_pool'] = Container.registered_objects.count()
    stats['verified_rate'] = _rate(stats['verified'], stats['confirmed'])
    stats['awaiting_rampe'] = stats['pending'] + stats['to_verify']
    stats['anomalies'] = stats['not_verified'] + stats['unavailable']

    prev_awaiting = prev_stats['pending'] + prev_stats['to_verify']
    prev_anomalies = prev_stats['not_verified'] + prev_stats['unavailable']

    stats['total_delta'] = _format_delta(_pct_delta(stats['total'], prev_stats['total']))
    stats['verified_delta'] = _format_delta(_pct_delta(stats['verified'], prev_stats['verified']))
    stats['awaiting_delta'] = _format_delta(_pct_delta(stats['awaiting_rampe'], prev_awaiting))
    stats['anomalies_delta'] = _format_delta(_pct_delta(stats['anomalies'], prev_anomalies))

    total_base = stats['total'] or 1
    stats['total_pct'] = 100 if stats['total'] else 0
    stats['verified_pct'] = min(100, round(100 * stats['verified'] / total_base)) if stats['total'] else 0
    stats['awaiting_pct'] = min(100, round(100 * stats['awaiting_rampe'] / total_base)) if stats['total'] else 0
    stats['anomalies_pct'] = min(100, round(100 * stats['anomalies'] / total_base)) if stats['total'] else 0

    show_verification = selected_date <= today

    # Répartition par ligne maritime (les conteneurs sans ligne sont regroupés).
    by_line = list(
        period_qs.values('shipping_line__name')
        .annotate(
            total=Count('id'),
            confirmed=Count('id', filter=Q(status=S.CONFIRMED)),
            pending=Count('id', filter=Q(status=S.PENDING)),
            unavailable=Count('id', filter=Q(status=S.UNAVAILABLE)),
        )
        .order_by('-total', 'shipping_line__name')
    )
    for row in by_line:
        row['name'] = row['shipping_line__name'] or 'Non renseignée'
        row['confirmed_pct'] = _rate(row['confirmed'], row['total']) or 0
        row['pending_pct'] = _rate(row['pending'], row['total']) or 0
        row['unavailable_pct'] = 100 - row['confirmed_pct'] - row['pending_pct'] if row['total'] else 0

    # Segments du graphique en anneau (top 4 + Autres, couleurs WCAG AA >= 3:1 sur blanc).
    donut_palette = ['#0284c7', '#475569', '#0f172a', '#b45309', '#64748b']
    donut_source = []
    if by_line:
        top_lines = by_line[:4]
        others_total = sum(r['total'] for r in by_line[4:])
        for r in top_lines:
            donut_source.append({'name': r['name'], 'total': r['total']})
        if others_total:
            donut_source.append({'name': 'Autres', 'total': others_total})

    donut_circumference = 2 * math.pi * 42
    donut_segments = []
    if stats['total'] and donut_source:
        offset = 0.0
        gap = 1.5 if len(donut_source) > 1 else 0.0
        for idx, item in enumerate(donut_source):
            seg_len = (item['total'] / stats['total']) * donut_circumference
            visible_len = max(1.0, seg_len - gap)
            donut_segments.append({
                'name': item['name'],
                'total': item['total'],
                'pct': round(100 * item['total'] / stats['total']),
                'color': donut_palette[idx % len(donut_palette)],
                'dash': f'{visible_len:.2f} {donut_circumference:.2f}',
                'offset': f'{-offset:.2f}',
            })
            offset += seg_len

    # Sept derniers jours jusqu'à la date affichée (conservé pour la compatibilité et la cadence).
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

    # Série du graphique combiné (7 jours par défaut, ou tranches pour 30 jours / plage personnalisée).
    if period in ('30d', 'custom'):
        buckets = []
        raw_range = {
            r['scheduled_date']: r
            for r in Container.objects.filter(scheduled_date__range=(period_start, period_end))
            .values('scheduled_date')
            .annotate(total=Count('id'), verified=Count('id', filter=Q(verification_result=V.VERIFIED)))
        }
        if period_days <= 10:
            for offset in range(period_days):
                day = period_start + datetime.timedelta(days=offset)
                row = raw_range.get(day, {})
                buckets.append({
                    'label': day.strftime('%d/%m'),
                    'total': row.get('total', 0),
                    'verified': row.get('verified', 0),
                })
        else:
            n_bins = 10
            for i in range(n_bins):
                s_idx = (i * period_days) // n_bins
                e_idx = ((i + 1) * period_days) // n_bins
                b_start = period_start + datetime.timedelta(days=s_idx)
                b_total = sum(
                    raw_range.get(period_start + datetime.timedelta(days=j), {}).get('total', 0)
                    for j in range(s_idx, e_idx)
                )
                b_verif = sum(
                    raw_range.get(period_start + datetime.timedelta(days=j), {}).get('verified', 0)
                    for j in range(s_idx, e_idx)
                )
                buckets.append({'label': b_start.strftime('%d/%m'), 'total': b_total, 'verified': b_verif})
    else:
        buckets = [
            {'label': d['date'].strftime('%d/%m'), 'total': d['total'], 'verified': d['verified']}
            for d in week
        ]

    # Coordonnées SVG exactes (viewBox 0 0 640 220, aire utile x=42..594, y=16..182)
    chart_peak = max((b['total'] for b in buckets), default=0)
    y_max = max(4, ((chart_peak + 3) // 4) * 4) if chart_peak else 4
    y_ticks = [
        {'val': y_max, 'y': 16},
        {'val': round(y_max * 0.75), 'y': 57},
        {'val': round(y_max * 0.5), 'y': 99},
        {'val': round(y_max * 0.25), 'y': 140},
        {'val': 0, 'y': 182},
    ]
    n_buckets = len(buckets) or 1
    slot_w = 552 / n_buckets
    bar_w = min(22, max(10, round(slot_w * 0.28)))
    bar_gap = 4
    plot_h = 166
    baseline_y = 182

    cadence_bars = []
    trend_points = []
    for idx, b in enumerate(buckets):
        cx = 42 + slot_w * (idx + 0.5)
        h_total = round((b['total'] / y_max) * plot_h) if y_max else 0
        h_verif = round((b['verified'] / y_max) * plot_h) if y_max else 0
        rate = round(100 * b['verified'] / b['total']) if b['total'] else 0
        y_trend = round(baseline_y - (rate / 100) * plot_h)
        cadence_bars.append({
            'label': b['label'],
            'cx': f'{cx:.1f}',
            'x_total': f'{cx - bar_w - bar_gap / 2:.1f}',
            'y_total': baseline_y - h_total,
            'h_total': h_total,
            'x_verif': f'{cx + bar_gap / 2:.1f}',
            'y_verif': baseline_y - h_verif,
            'h_verif': h_verif,
            'bar_w': bar_w,
            'total': b['total'],
            'verified': b['verified'],
            'rate': rate,
            'y_trend': y_trend,
        })
        trend_points.append(f'{cx:.1f},{y_trend}')

    cadence_chart = {
        'bars': cadence_bars,
        'trend_polyline': ' '.join(trend_points),
        'y_ticks': y_ticks,
        'has_data': any(b['total'] or b['verified'] for b in buckets),
    }

    unavailable_motifs_stats = _reason_breakdown(
        period_qs, {'status': S.UNAVAILABLE}, 'unavailability_reason__label'
    )[:5]

    priority_qs = (
        period_qs.select_related('shipping_line')
        .filter(
            Q(status__in=[S.PENDING, S.UNAVAILABLE])
            | Q(status=S.CONFIRMED, verification_result='')
            | Q(verification_result=V.NOT_VERIFIED)
        )
        .order_by('first_scheduled_date', 'scheduled_date', 'id')[:6]
    )
    priority_queue = []
    for c in priority_qs:
        ref_date = c.first_scheduled_date or c.scheduled_date or today
        days_waiting = max(0, (today - ref_date).days)
        if c.verification_result == V.NOT_VERIFIED:
            status_text = 'Non vérifié'
            status_class = 'text-status-pending-fg'
        elif c.status == S.UNAVAILABLE:
            status_text = 'Indisponible'
            status_class = 'text-status-unavailable-fg'
        elif c.status == S.CONFIRMED:
            status_text = 'À inspecter'
            status_class = 'text-primary'
        else:
            status_text = 'En attente'
            status_class = 'text-status-pending-fg'

        priority_queue.append({
            'pk': c.pk,
            'container_number': c.container_number,
            'carrier': c.shipping_line.name if c.shipping_line else 'Non renseigné',
            'status_text': status_text,
            'status_class': status_class,
            'days': days_waiting,
        })

    backlog = _backlog_breakdown(unverified_backlog(default_carry_target(request.user)))
    recent_logs = (
        ContainerStatusLog.objects.select_related('container', 'changed_by')
        .filter(container__scheduled_date=selected_date)
        .order_by('-changed_at')[:8]
    )

    return render(request, 'portal/dashboard/index.html', {
        'today': today,
        'tomorrow': today + datetime.timedelta(days=1),
        'selected_date': selected_date,
        'previous_date': selected_date - datetime.timedelta(days=1),
        'next_date': selected_date + datetime.timedelta(days=1),
        'period': period,
        'period_start': period_start,
        'period_end': period_end,
        'show_verification': show_verification,
        'stats': stats,
        'by_line': by_line,
        'donut_segments': donut_segments,
        'week': week,
        'cadence_chart': cadence_chart,
        'unavailable_motifs_stats': unavailable_motifs_stats,
        'priority_queue': priority_queue,
        'backlog': backlog,
        'recent_logs': recent_logs,
    })


