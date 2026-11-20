"""Rapports d'exploitation : statistiques, registre, historique et exports CSV/PDF."""

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

from terminal.services.reports import (
    _STAT_KEYS,
    _period_bounds,
    _rate,
    _reason_breakdown,
    _stat_annotations,
)


@require_permission(Actions.REPORT_VIEW)
def register_view(request):
    """Registre des vérifications : qui a été vérifié, quand, et les quantités sur un intervalle."""
    today = timezone.localdate()
    search_q = request.GET.get('q', '').strip()
    result_filter = request.GET.get('resultat', '').strip()
    line_filter = request.GET.get('line', '').strip()
    has_dates = bool(request.GET.get('du') or request.GET.get('au'))

    qs = Container.objects.select_related('shipping_line', 'verified_by')
    # Une recherche de numéro sans dates porte sur tout l'historique : on veut savoir à quelle
    # date le conteneur a été vérifié, quelle qu'elle soit.
    if search_q and not has_dates:
        start = end = None
    else:
        start = _parse_date(request.GET.get('du', ''), today - datetime.timedelta(days=29))
        end = _parse_date(request.GET.get('au', ''), today)
        if end < start:
            start, end = end, start
        qs = qs.filter(scheduled_date__gte=start, scheduled_date__lte=end)

    if search_q:
        qs = qs.filter(container_number__icontains=normalize_container_number(search_q))
    if line_filter.isdigit():
        qs = qs.filter(shipping_line_id=int(line_filter))

    # Quantités de l'intervalle, avant le filtre par résultat.
    totals = qs.aggregate(**_stat_annotations())
    totals['distinct'] = qs.values('container_number').distinct().count()
    totals['rate'] = _rate(totals['verified'], totals['confirmed'])

    rows = qs
    if result_filter == 'to_verify':
        rows = rows.filter(status=Container.Status.CONFIRMED, verification_result='')
    elif result_filter in Container.Verification.values:
        rows = rows.filter(verification_result=result_filter)
    rows = rows.order_by('-scheduled_date', 'container_number')

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        label = f"{start.isoformat()}_{end.isoformat()}" if start else 'recherche'
        response['Content-Disposition'] = f'attachment; filename="CTSA_RAMPE_registre_{label}.csv"'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        writer.writerow([
            'Numero Conteneur', 'Ligne Maritime', 'Seance', 'Statut', 'Verification',
            'Verifie Le', 'Marque Par', 'Precision / Motif', 'Premiere Programmation', 'Report',
        ])
        for c in rows:
            writer.writerow([
                c.container_number,
                c.shipping_line.name if c.shipping_line else '',
                c.scheduled_date.strftime('%d/%m/%Y'),
                c.get_status_display(),
                c.get_verification_result_display(),
                timezone.localtime(c.verified_at).strftime('%d/%m/%Y %H:%M') if c.verified_at else '',
                c.verified_by.display_name if c.verified_by else '',
                c.verification_note or c.full_reason_display,
                (c.first_scheduled_date or c.scheduled_date).strftime('%d/%m/%Y'),
                c.get_carry_decision_display(),
            ])
        return response

    page, page_query = _paginate(request, rows)
    return render(request, 'portal/register.html', {
        'today': today,
        'start': start,
        'end': end,
        'search_q': search_q,
        'result_filter': result_filter,
        'line_filter': line_filter,
        'totals': totals,
        'rows': page.object_list,
        'page': page,
        'page_query': page_query,
        'shipping_lines': ShippingLine.objects.filter(is_active=True),
    })




@require_permission(Actions.REPORT_VIEW)
def statistics_view(request):
    today = timezone.localdate()
    kind = request.GET.get('periode', 'semaine')
    if kind not in ('semaine', 'mois', 'perso'):
        kind = 'semaine'
    ref = _parse_date(request.GET.get('ref', ''), today)

    if kind == 'perso':
        start = _parse_date(request.GET.get('du', ''), today - datetime.timedelta(days=6))
        end = _parse_date(request.GET.get('au', ''), today)
        if end < start:
            start, end = end, start
        if (end - start).days > 366:
            end = start + datetime.timedelta(days=366)
        prev_ref = next_ref = None
    else:
        start, end = _period_bounds(kind, ref)
        prev_ref = start - datetime.timedelta(days=1)
        next_ref = end + datetime.timedelta(days=1)

    qs = Container.objects.filter(scheduled_date__gte=start, scheduled_date__lte=end)
    totals = qs.aggregate(**_stat_annotations())
    totals['distinct'] = qs.values('container_number').distinct().count()
    totals['rate'] = _rate(totals['verified'], totals['confirmed'])

    # Détail par jour, regroupé par semaine dès que la période dépasse deux semaines.
    per_day = {
        row['scheduled_date']: row
        for row in qs.values('scheduled_date').annotate(**_stat_annotations())
    }
    by_week = (end - start).days > 13
    buckets = []
    cursor = start
    while cursor <= end:
        if by_week:
            bucket_end = min(cursor + datetime.timedelta(days=6 - cursor.weekday()), end)
            label = f"Du {cursor.strftime('%d/%m')} au {bucket_end.strftime('%d/%m')}"
        else:
            bucket_end = cursor
            label = cursor.strftime('%d/%m/%Y')
        bucket = {'label': label, 'start': cursor, 'end': bucket_end, **{k: 0 for k in _STAT_KEYS}}
        day = cursor
        while day <= bucket_end:
            for k in _STAT_KEYS:
                bucket[k] += per_day.get(day, {}).get(k, 0)
            day += datetime.timedelta(days=1)
        bucket['rate'] = _rate(bucket['verified'], bucket['confirmed'])
        buckets.append(bucket)
        cursor = bucket_end + datetime.timedelta(days=1)
    max_scheduled = max((b['scheduled'] for b in buckets), default=0)
    for b in buckets:
        b['bar_scheduled'] = round(100 * b['scheduled'] / max_scheduled) if max_scheduled else 0
        b['bar_verified'] = round(100 * b['verified'] / max_scheduled) if max_scheduled else 0

    lines = list(
        qs.values('shipping_line__name').annotate(**_stat_annotations()).order_by('-scheduled')
    )
    for row in lines:
        row['name'] = row['shipping_line__name'] or 'Non renseignée'
        row['rate'] = _rate(row['verified'], row['confirmed'])

    # Délai moyen entre la première programmation et la vérification, sur la période.
    delays = [
        (timezone.localtime(verified_at).date() - first).days
        for verified_at, first in qs.filter(
            verification_result=Container.Verification.VERIFIED,
            verified_at__isnull=False,
            first_scheduled_date__isnull=False,
        ).values_list('verified_at', 'first_scheduled_date')
    ]
    avg_delay = round(sum(delays) / len(delays), 1) if delays else None

    # Répartition par origine (Programmé / Arrivé sans liste)
    origin_labels = dict(Container.Origin.choices)
    by_origin = []
    for row in qs.values('origin').annotate(**_stat_annotations()).order_by('origin'):
        row['label'] = origin_labels.get(row['origin'], row['origin'])
        row['rate'] = _rate(row['verified'], row['confirmed'])
        by_origin.append(row)

    # Non vérifiés par motif et Indisponibles par motif
    not_verified_by_reason = _reason_breakdown(
        qs, {'verification_result': Container.Verification.NOT_VERIFIED}, 'verification_reason__label'
    )
    unavailable_by_reason = _reason_breakdown(
        qs, {'status': Container.Status.UNAVAILABLE}, 'unavailability_reason__label'
    )

    # Conteneurs enregistrés sans liste depuis >= N jours
    alert_days = SiteSettings.get_solo().unscheduled_alert_days or 3
    cutoff = timezone.now() - datetime.timedelta(days=alert_days)
    stale_registered = list(
        Container.registered_objects.filter(arrived_at__lte=cutoff)
        .select_related('shipping_line')
        .order_by('arrived_at')[:50]
    )

    # Situation actuelle du suivi (indépendante de la période affichée).
    backlog_qs = unverified_backlog(today)
    backlog = _backlog_breakdown(backlog_qs)
    oldest = backlog_qs.order_by('first_scheduled_date').first()

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = (
            f'attachment; filename="CTSA_RAMPE_statistiques_{start.isoformat()}_{end.isoformat()}.csv"'
        )
        response.write('﻿')
        writer = csv.writer(response, delimiter=';')
        writer.writerow([
            'Periode', 'Programmes', 'Confirmes', 'Verifies', 'Restent a inspecter',
            'Repris', 'Rejetes', 'Taux de verification (%)',
        ])
        for b in buckets + [{'label': 'TOTAL', **totals}]:
            writer.writerow([
                b['label'], b['scheduled'], b['confirmed'], b['verified'],
                b['to_verify'], b['carried'], b['rejected'], '' if b['rate'] is None else b['rate'],
            ])
        return response

    show_lines = len(lines) > 1 or (len(lines) == 1 and lines[0]['shipping_line__name'] is not None)

    return render(request, 'portal/statistics.html', {
        'today': today,
        'kind': kind,
        'ref': ref,
        'start': start,
        'end': end,
        'prev_ref': prev_ref,
        'next_ref': next_ref,
        'totals': totals,
        'buckets': buckets,
        'by_week': by_week,
        'lines': lines,
        'show_lines': show_lines,
        'avg_delay': avg_delay,
        'verified_with_delay': len(delays),
        'backlog': backlog,
        'oldest': oldest,
        'by_origin': by_origin,
        'not_verified_by_reason': not_verified_by_reason,
        'unavailable_by_reason': unavailable_by_reason,
        'stale_registered': stale_registered,
        'unscheduled_alert_days': alert_days,
    })


@require_permission(Actions.REPORT_VIEW)
def container_export_pdf_view(request):
    import io
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    today = timezone.localdate()
    selected_date = _parse_date(request.GET.get('date', ''), today)
    status_filter = request.GET.get('status', '').strip()
    line_filter = request.GET.get('line', '').strip()

    qs = Container.objects.filter(scheduled_date=selected_date).select_related(
        'shipping_line', 'updated_by'
    ).order_by('status', 'container_number')

    status_label = "Tous les statuts"
    if status_filter in dict(Container.Status.choices):
        qs = qs.filter(status=status_filter)
        status_label = dict(Container.Status.choices)[status_filter]
    elif status_filter == 'confirmed_pending':
        qs = qs.filter(status__in=[Container.Status.CONFIRMED, Container.Status.PENDING])
        status_label = "Confirmés et En attente"

    if line_filter.isdigit():
        qs = qs.filter(shipping_line_id=int(line_filter))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"CTSA RAMPE - {selected_date.strftime('%d/%m/%Y')}",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CTSATitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=15,
        textColor=colors.HexColor('#2c3741'),
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        'CTSASubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        textColor=colors.HexColor('#59626a'),
        spaceAfter=14,
    )
    cell_style = ParagraphStyle(
        'CTSACell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        textColor=colors.HexColor('#333333'),
    )

    elements = []
    elements.append(Paragraph("CAP TERMINAL S.A. — PORT DU CAP-HAÏTIEN", title_style))
    elements.append(
        Paragraph(
            f"Liste de vérification douanière RAMPE du <b>{selected_date.strftime('%d/%m/%Y')}</b> "
            f"— Filtre : <b>{status_label}</b> — Total : <b>{qs.count()} conteneur(s)</b>",
            subtitle_style,
        )
    )

    table_data = [[
        '#',
        'Numéro de conteneur',
        'Ligne maritime',
        'Statut',
        'Vérification',
        'Observation / Motif',
    ]]

    for idx, c in enumerate(qs, start=1):
        if c.status == Container.Status.UNAVAILABLE:
            obs = c.full_reason_display
        else:
            obs = c.verification_note or '-'
        table_data.append([
            str(idx),
            c.container_number,
            c.shipping_line.name if c.shipping_line else '-',
            c.get_status_display(),
            c.get_verification_result_display() or '-',
            Paragraph(obs, cell_style),
        ])

    if len(table_data) == 1:
        table_data.append(['-', 'Aucun conteneur pour cette sélection', '-', '-', '-', '-'])

    pdf_table = Table(
        table_data,
        colWidths=[10 * mm, 38 * mm, 40 * mm, 27 * mm, 27 * mm, 38 * mm],
        repeatRows=1,
    )
    pdf_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3741')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('FONTNAME', (1, 1), (1, -1), 'Courier-Bold'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f4f4f4')]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dadada')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 1), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
    ]))
    elements.append(pdf_table)
    elements.append(Spacer(1, 10 * mm))
    elements.append(
        Paragraph(
            f"Document édité le {timezone.localtime(timezone.now()).strftime('%d/%m/%Y à %H:%M')} "
            f"par {request.user.display_name}.",
            subtitle_style,
        )
    )

    doc.build(elements)
    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    suffix = status_filter if status_filter else 'tous'
    filename = f"CTSA_RAMPE_{selected_date.isoformat()}_{suffix}.pdf"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@require_permission(Actions.REPORT_VIEW)
def container_export_csv_view(request):
    today = timezone.localdate()
    selected_date = _parse_date(request.GET.get('date', ''), today)
    status_filter = request.GET.get('status', '').strip()

    qs = Container.objects.filter(scheduled_date=selected_date).select_related(
        'shipping_line', 'prefix_ref', 'updated_by', 'unavailability_reason', 'verification_reason'
    ).order_by('container_number')

    if status_filter in dict(Container.Status.choices):
        qs = qs.filter(status=status_filter)

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    filename = f"CTSA_RAMPE_{selected_date.isoformat()}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    # Write UTF-8 BOM for Excel compatibility
    response.write('\ufeff')

    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'Numero Conteneur',
        'Prefixe',
        'Origine',
        'Ligne Maritime',
        'Date Programmation RAMPE',
        'Statut',
        'Motif Indisponibilite',
        'Precision Indisponibilite',
        'Verification',
        'Motif Non-Verification',
        'Precision Non-Verification',
        'Premiere Programmation',
        'Derniere Mise a Jour',
        'Modifie Par',
    ])

    for c in qs:
        writer.writerow([
            c.container_number,
            c.prefix_code,
            c.get_origin_display(),
            c.shipping_line.name if c.shipping_line else 'Non rattaché',
            c.scheduled_date.strftime('%d/%m/%Y') if c.scheduled_date else '',
            c.get_status_display(),
            c.unavailability_reason.label if c.unavailability_reason else '',
            c.unavailability_note if c.status == Container.Status.UNAVAILABLE else '',
            c.get_verification_result_display() or 'Non inspecté',
            c.verification_reason.label if c.verification_reason else '',
            c.verification_note if c.verification_result == Container.Verification.NOT_VERIFIED else '',
            (c.first_scheduled_date or c.scheduled_date).strftime('%d/%m/%Y') if (c.first_scheduled_date or c.scheduled_date) else '',
            timezone.localtime(c.updated_at).strftime('%d/%m/%Y %H:%M'),
            c.updated_by.display_name if c.updated_by else '-',
        ])

    return response




@require_permission(Actions.REPORT_VIEW)
def status_logs_view(request):
    q = request.GET.get('q', '').strip()
    logs = ContainerStatusLog.objects.select_related(
        'container', 'container__shipping_line', 'changed_by'
    ).order_by('-changed_at')

    if q:
        clean_q = normalize_container_number(q)
        logs = logs.filter(
            Q(container__container_number__icontains=clean_q)
            | Q(reason_text__icontains=q)
            | Q(changed_by__username__icontains=q)
            | Q(changed_by__full_name__icontains=q)
        )

    archived_containers = list(
        Container.all_objects.filter(archived_at__isnull=False)
        .select_related('shipping_line', 'archived_by')
        .order_by('-archived_at')[:30]
    )

    page, page_query = _paginate(request, logs)
    return render(request, 'portal/status_logs.html', {
        'logs': page.object_list,
        'page': page,
        'page_query': page_query,
        'q': q,
        'archived_containers': archived_containers,
    })


