import csv
import datetime
import re
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    ShippingLine,
    ContainerPrefix,
    UnavailabilityReason,
    Container,
    ContainerStatusLog,
    SavedContainer,
    normalize_container_number,
    CONTAINER_NUMBER_REGEX,
)

# Create your views here.


def _parse_date(date_str: str, default_date: datetime.date) -> datetime.date:
    if not date_str:
        return default_date
    try:
        return datetime.date.fromisoformat(date_str)
    except ValueError:
        return default_date


def _pick_relevant_schedule(containers, today: datetime.date):
    """Choisit la programmation à montrer au client pour un même numéro de conteneur.

    Priorité : séance du jour, sinon prochaine séance à venir, sinon dernière séance passée.
    L'objet retourné porte un attribut `timing` ('today', 'upcoming' ou 'past') : seul un
    statut de la séance du jour autorise un déplacement.
    """
    chosen = None
    chosen_rank = None
    for c in containers:
        delta = (c.scheduled_date - today).days
        if delta == 0:
            rank = (0, 0)
        elif delta > 0:
            rank = (1, delta)
        else:
            rank = (2, -delta)
        if chosen_rank is None or rank < chosen_rank:
            chosen, chosen_rank = c, rank
    if chosen is not None:
        chosen.timing = ('today', 'upcoming', 'past')[chosen_rank[0]]
    return chosen


def home_view(request):
    today = timezone.localdate()
    shipping_lines = ShippingLine.objects.filter(is_active=True).order_by('name')
    return render(request, 'public/home.html', {
        'today': today,
        'shipping_lines': shipping_lines,
    })


def verification_view(request):
    raw_query = request.GET.get('q', '').strip()
    normalized = normalize_container_number(raw_query)
    today = timezone.localdate()

    result_today = None
    format_warning = None
    is_saved = False

    if normalized:
        if not CONTAINER_NUMBER_REGEX.match(normalized):
            format_warning = (
                "Le numéro de conteneur doit comporter exactement 4 lettres suivies de 7 chiffres (exemple : CMAU4052450)."
            )
        else:
            matches = list(
                Container.objects.select_related(
                    'shipping_line', 'unavailability_reason'
                )
                .filter(container_number=normalized)
                .order_by('-scheduled_date', '-updated_at')
            )

            result_today = _pick_relevant_schedule(matches, today)

            if request.user.is_authenticated:
                is_saved = SavedContainer.objects.filter(
                    user=request.user, container_number=normalized
                ).exists()

    return render(request, 'public/verification.html', {
        'query': raw_query,
        'normalized': normalized,
        'today': today,
        'result': result_today,
        'format_warning': format_warning,
        'is_saved': is_saved,
    })


@login_required
def client_watchlist_view(request):
    today = timezone.localdate()
    if request.method == 'POST':
        raw_num = request.POST.get('container_number', '')
        label = request.POST.get('label', '').strip()
        notify_whatsapp = request.POST.get('notify_whatsapp') == 'on'
        normalized = normalize_container_number(raw_num)

        if not CONTAINER_NUMBER_REGEX.match(normalized):
            messages.error(
                request,
                "Numéro invalide. Veuillez saisir 4 lettres suivies de 7 chiffres (ex. CMAU4052450)."
            )
        else:
            obj, created = SavedContainer.objects.update_or_create(
                user=request.user,
                container_number=normalized,
                defaults={'label': label, 'notify_whatsapp': notify_whatsapp},
            )
            if created:
                messages.success(request, f"Le conteneur {normalized} a été ajouté à votre suivi.")
            else:
                messages.info(request, f"Le suivi du conteneur {normalized} a été mis à jour.")
        return redirect('terminal:client_watchlist')

    saved_list = list(SavedContainer.objects.filter(user=request.user))
    numbers = [s.container_number for s in saved_list]

    containers_by_num = {}
    if numbers:
        grouped = {}
        for c in (
            Container.objects.select_related('shipping_line', 'unavailability_reason')
            .filter(container_number__in=numbers)
            .order_by('container_number', '-scheduled_date', '-updated_at')
        ):
            grouped.setdefault(c.container_number, []).append(c)
        containers_by_num = {
            num: _pick_relevant_schedule(items, today) for num, items in grouped.items()
        }

    enriched_watchlist = []
    for item in saved_list:
        prefix_obj = ContainerPrefix.objects.select_related('shipping_line').filter(
            prefix=item.container_number[:4]
        ).first()
        enriched_watchlist.append({
            'saved': item,
            'container': containers_by_num.get(item.container_number),
            'prefix_obj': prefix_obj,
        })

    return render(request, 'public/watchlist.html', {
        'watchlist': enriched_watchlist,
        'today': today,
    })


@login_required
@require_POST
def client_watchlist_remove_view(request, pk):
    saved = get_object_or_404(SavedContainer, pk=pk, user=request.user)
    num = saved.container_number
    saved.delete()
    messages.info(request, f"Le conteneur {num} a été retiré de votre liste de suivi.")
    return redirect('terminal:client_watchlist')


@login_required
def dashboard_view(request):
    if not request.user.is_staff_member:
        return redirect('terminal:client_watchlist')

    today = timezone.localdate()
    tomorrow = today + datetime.timedelta(days=1)

    default_date = tomorrow if request.user.is_evening_supervisor else today
    selected_date = _parse_date(request.GET.get('date', ''), default_date)
    status_filter = request.GET.get('status', '').strip()
    line_filter = request.GET.get('line', '').strip()
    search_q = request.GET.get('q', '').strip()

    base_qs = Container.objects.filter(scheduled_date=selected_date).select_related(
        'shipping_line', 'prefix_ref', 'unavailability_reason', 'created_by', 'updated_by'
    )

    stats = {
        'total': base_qs.count(),
        'confirmed': base_qs.filter(status=Container.Status.CONFIRMED).count(),
        'pending': base_qs.filter(status=Container.Status.PENDING).count(),
        'unavailable': base_qs.filter(status=Container.Status.UNAVAILABLE).count(),
    }

    containers = base_qs
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

    shipping_lines = ShippingLine.objects.filter(is_active=True)
    reasons = UnavailabilityReason.objects.filter(is_active=True)
    recent_logs = (
        ContainerStatusLog.objects.select_related('container', 'changed_by')
        .filter(container__scheduled_date=selected_date)
        .order_by('-changed_at')[:10]
    )

    return render(request, 'portal/dashboard.html', {
        'today': today,
        'tomorrow': tomorrow,
        'selected_date': selected_date,
        'status_filter': status_filter,
        'line_filter': line_filter,
        'search_q': search_q,
        'stats': stats,
        'containers': containers,
        'shipping_lines': shipping_lines,
        'reasons': reasons,
        'recent_logs': recent_logs,
        'status_choices': Container.Status.choices,
    })


@login_required
@require_POST
def container_add_single_view(request):
    if not request.user.is_staff_member:
        return redirect('terminal:home')

    today = timezone.localdate()
    tomorrow = today + datetime.timedelta(days=1)
    default_date = tomorrow if request.user.is_evening_supervisor else today

    raw_number = request.POST.get('container_number', '')
    normalized = normalize_container_number(raw_number)
    target_date = _parse_date(request.POST.get('scheduled_date', ''), default_date)

    # Morning supervisor restriction: strictly 'pending' status
    if request.user.is_morning_supervisor:
        status = Container.Status.PENDING
        target_date = today
    else:
        status = request.POST.get('status', Container.Status.PENDING)
        if status not in dict(Container.Status.choices):
            status = Container.Status.PENDING

    shipping_line_id = request.POST.get('shipping_line') or ''
    reason_id = request.POST.get('unavailability_reason') or None
    note = request.POST.get('unavailability_note', '').strip()

    if not CONTAINER_NUMBER_REGEX.match(normalized):
        messages.error(
            request,
            f"Numéro « {raw_number} » invalide : il doit comporter 4 lettres et 7 chiffres (ex. CMAU4052450)."
        )
        return redirect(f"/admin/?date={target_date.isoformat()}")

    if not shipping_line_id.isdigit():
        messages.error(
            request,
            "Veuillez sélectionner manuellement la ligne maritime du conteneur."
        )
        return redirect(f"/admin/?date={target_date.isoformat()}")

    existing = Container.objects.filter(
        container_number=normalized, scheduled_date=target_date
    ).first()

    if existing:
        if request.user.is_morning_supervisor:
            messages.warning(
                request,
                f"Le conteneur {normalized} est déjà programmé pour le {target_date.strftime('%d/%m/%Y')}."
            )
            return redirect(f"/admin/?date={target_date.isoformat()}")
        existing.status = status
        existing.shipping_line_id = int(shipping_line_id)
        existing.unavailability_reason_id = int(reason_id) if (reason_id and reason_id.isdigit()) else None
        existing.unavailability_note = note
        existing.save(acting_user=request.user)
        messages.info(
            request,
            f"Le conteneur existant {normalized} a été mis à jour ({existing.get_status_display()})."
        )
    else:
        container = Container(
            container_number=normalized,
            scheduled_date=target_date,
            status=status,
            shipping_line_id=int(shipping_line_id),
            unavailability_reason_id=int(reason_id) if (reason_id and reason_id.isdigit()) else None,
            unavailability_note=note,
        )
        container.save(acting_user=request.user)
        line_info = f" ({container.shipping_line.name})" if container.shipping_line else ""
        messages.success(
            request,
            f"Conteneur {normalized}{line_info} ajouté avec le statut « {container.get_status_display()} »."
        )

    return redirect(f"/admin/?date={target_date.isoformat()}")


@login_required
@require_POST
def container_add_bulk_view(request):
    if not request.user.is_staff_member:
        return redirect('terminal:home')

    today = timezone.localdate()
    tomorrow = today + datetime.timedelta(days=1)
    default_date = tomorrow if request.user.is_evening_supervisor else today

    raw_list = request.POST.get('container_list', '')
    target_date = _parse_date(request.POST.get('scheduled_date', ''), default_date)
    shipping_line_id = request.POST.get('shipping_line') or ''

    if request.user.is_morning_supervisor:
        default_status = Container.Status.PENDING
        target_date = today
    else:
        default_status = request.POST.get('default_status', Container.Status.PENDING)
        if default_status not in dict(Container.Status.choices):
            default_status = Container.Status.PENDING

    if not shipping_line_id.isdigit():
        messages.error(request, "Veuillez sélectionner la ligne maritime pour ce lot de conteneurs.")
        return redirect(f"/admin/?date={target_date.isoformat()}")

    tokens = [t.strip() for t in re.split(r'[\r\n,;]+', raw_list) if t.strip()]
    if not tokens:
        messages.warning(request, "Aucun numéro de conteneur fourni dans la zone d'ajout massif.")
        return redirect(f"/admin/?date={target_date.isoformat()}")

    created_count = 0
    skipped_duplicates = []
    invalid_tokens = []

    for token in tokens:
        normalized = normalize_container_number(token)
        if not CONTAINER_NUMBER_REGEX.match(normalized):
            invalid_tokens.append(token)
            continue

        if Container.objects.filter(container_number=normalized, scheduled_date=target_date).exists():
            skipped_duplicates.append(normalized)
            continue

        c = Container(
            container_number=normalized,
            scheduled_date=target_date,
            status=default_status,
            shipping_line_id=int(shipping_line_id),
        )
        c.save(acting_user=request.user)
        created_count += 1

    if created_count:
        messages.success(
            request,
            f"Ajout massif terminé : {created_count} conteneur(s) programmé(s) pour le {target_date.strftime('%d/%m/%Y')}."
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

    return redirect(f"/admin/?date={target_date.isoformat()}")


@login_required
@require_POST
def container_update_status_view(request, pk):
    if not request.user.can_edit_or_delete_containers:
        messages.error(
            request,
            "Le rôle Superviseur matin permet uniquement d'ajouter des conteneurs en statut « En attente »."
        )
        return redirect('terminal:dashboard')

    container = get_object_or_404(Container, pk=pk)
    new_status = request.POST.get('status', container.status)
    reason_id = request.POST.get('unavailability_reason') or None
    note = request.POST.get('unavailability_note', '').strip()
    shipping_line_id = request.POST.get('shipping_line') or None

    if new_status in dict(Container.Status.choices):
        container.status = new_status
        if shipping_line_id and shipping_line_id.isdigit():
            container.shipping_line_id = int(shipping_line_id)
        if new_status == Container.Status.UNAVAILABLE:
            if reason_id and reason_id.isdigit():
                container.unavailability_reason_id = int(reason_id)
            elif 'unavailability_reason' in request.POST and reason_id is None:
                container.unavailability_reason = None
            if 'unavailability_note' in request.POST:
                container.unavailability_note = note
        else:
            container.unavailability_reason = None
            container.unavailability_note = ''
        container.save(acting_user=request.user)
        messages.success(
            request,
            f"Conteneur {container.container_number} mis à jour ({container.get_status_display()})."
        )

    return redirect(f"/admin/?date={container.scheduled_date.isoformat()}")


@login_required
@require_POST
def container_delete_view(request, pk):
    if not request.user.can_edit_or_delete_containers:
        messages.error(request, "Vous n'avez pas l'autorisation de supprimer un conteneur.")
        return redirect('terminal:dashboard')

    container = get_object_or_404(Container, pk=pk)
    date_iso = container.scheduled_date.isoformat()
    num = container.container_number
    container.delete()
    messages.info(request, f"Le conteneur {num} a été supprimé de la liste du {date_iso}.")
    return redirect(f"/admin/?date={date_iso}")


@login_required
def container_export_pdf_view(request):
    if not request.user.is_staff_member:
        return redirect('terminal:home')

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
        'shipping_line', 'unavailability_reason', 'updated_by'
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
        'Observation / Motif',
    ]]

    for idx, c in enumerate(qs, start=1):
        obs = c.full_reason_display if c.status == Container.Status.UNAVAILABLE else '-'
        table_data.append([
            str(idx),
            c.container_number,
            c.shipping_line.name if c.shipping_line else '-',
            c.get_status_display(),
            Paragraph(obs, cell_style),
        ])

    if len(table_data) == 1:
        table_data.append(['-', 'Aucun conteneur pour cette sélection', '-', '-', '-'])

    pdf_table = Table(
        table_data,
        colWidths=[12 * mm, 42 * mm, 45 * mm, 32 * mm, 49 * mm],
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


@login_required
def container_export_csv_view(request):
    if not request.user.is_staff_member:
        return redirect('terminal:home')

    today = timezone.localdate()
    selected_date = _parse_date(request.GET.get('date', ''), today)
    status_filter = request.GET.get('status', '').strip()

    qs = Container.objects.filter(scheduled_date=selected_date).select_related(
        'shipping_line', 'prefix_ref', 'unavailability_reason', 'updated_by'
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
        'Ligne Maritime',
        'Date Programmation RAMPE',
        'Statut',
        'Motif Indisponibilite',
        'Derniere Mise a Jour',
        'Modifie Par',
    ])

    for c in qs:
        writer.writerow([
            c.container_number,
            c.prefix_code,
            c.shipping_line.name if c.shipping_line else 'Non rattaché',
            c.scheduled_date.strftime('%d/%m/%Y'),
            c.get_status_display(),
            c.full_reason_display,
            timezone.localtime(c.updated_at).strftime('%d/%m/%Y %H:%M'),
            c.updated_by.display_name if c.updated_by else '-',
        ])

    return response


@login_required
def status_logs_view(request):
    if not request.user.is_staff_member:
        return redirect('terminal:home')

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

    return render(request, 'portal/status_logs.html', {
        'logs': logs[:150],
        'q': q,
    })


@login_required
def prefixes_view(request):
    if not request.user.is_portal_admin:
        messages.error(request, "Accès réservé aux administrateurs.")
        return redirect('terminal:dashboard')

    if request.method == 'POST':
        action = request.POST.get('action', 'add_prefix')
        if action == 'add_prefix':
            prefix = (request.POST.get('prefix') or '').strip().upper()
            line_id = request.POST.get('shipping_line')
            description = (request.POST.get('description') or '').strip()
            if not re.match(r'^[A-Z]{4}$', prefix):
                messages.error(request, "Le préfixe doit contenir exactement 4 lettres (ex. CMAU).")
            elif not line_id or not line_id.isdigit():
                messages.error(request, "Veuillez sélectionner une ligne maritime.")
            else:
                obj, created = ContainerPrefix.objects.update_or_create(
                    prefix=prefix,
                    defaults={
                        'shipping_line_id': int(line_id),
                        'description': description,
                    },
                )
                # Backfill existing containers matching this 4-letter prefix
                Container.objects.filter(container_number__startswith=prefix).update(
                    prefix_ref=obj, shipping_line_id=int(line_id)
                )
                messages.success(
                    request,
                    f"Préfixe {prefix} rattaché à {obj.shipping_line.name} avec succès."
                )
        elif action == 'delete_prefix':
            pk = request.POST.get('prefix_id')
            pref = get_object_or_404(ContainerPrefix, pk=pk)
            code = pref.prefix
            pref.delete()
            messages.info(request, f"Le préfixe {code} a été supprimé.")
        elif action == 'add_reason':
            label = (request.POST.get('label') or '').strip()
            description = (request.POST.get('description') or '').strip()
            if label:
                from django.utils.text import slugify
                code = slugify(label)[:40] or 'motif'
                UnavailabilityReason.objects.update_or_create(
                    code=code,
                    defaults={'label': label, 'description': description, 'is_active': True},
                )
                messages.success(request, f"Motif d'indisponibilité « {label} » enregistré.")
        elif action == 'delete_reason':
            pk = request.POST.get('reason_id')
            reason = get_object_or_404(UnavailabilityReason, pk=pk)
            reason.delete()
            messages.info(request, "Motif d'indisponibilité supprimé.")

        return redirect('terminal:prefixes')

    prefixes = ContainerPrefix.objects.select_related('shipping_line').annotate(
        container_count=Count('containers')
    )
    shipping_lines = ShippingLine.objects.filter(is_active=True)
    reasons = UnavailabilityReason.objects.annotate(usage_count=Count('containers'))

    return render(request, 'portal/prefixes.html', {
        'prefixes': prefixes,
        'shipping_lines': shipping_lines,
        'reasons': reasons,
    })


@login_required
def shipping_lines_view(request):
    if not request.user.is_portal_admin:
        messages.error(request, "Accès réservé aux administrateurs.")
        return redirect('terminal:dashboard')

    if request.method == 'POST':
        action = request.POST.get('action', 'save_line')
        if action == 'save_line':
            line_id = request.POST.get('line_id')
            name = (request.POST.get('name') or '').strip()

            if not name:
                messages.error(request, "Le nom de la ligne maritime est obligatoire.")
            else:
                from django.utils.text import slugify
                auto_code = slugify(name)[:20].upper()
                if line_id and line_id.isdigit():
                    line = get_object_or_404(ShippingLine, pk=int(line_id))
                    if ShippingLine.objects.filter(name__iexact=name).exclude(pk=line.pk).exists():
                        messages.warning(request, f"Une ligne maritime nommée « {name} » existe déjà.")
                    else:
                        line.name = name
                        line.code = auto_code
                        line.save()
                        messages.success(request, f"Ligne maritime renommée en « {name} ».")
                else:
                    if ShippingLine.objects.filter(name__iexact=name).exists():
                        messages.warning(request, f"La ligne maritime « {name} » existe déjà.")
                    else:
                        ShippingLine.objects.create(
                            name=name,
                            code=auto_code,
                        )
                        messages.success(request, f"Ligne maritime « {name} » ajoutée.")
        elif action == 'delete_line':
            pk = request.POST.get('line_id')
            line = get_object_or_404(ShippingLine, pk=pk)
            name = line.name
            line.delete()
            messages.info(request, f"Ligne maritime « {name} » supprimée.")

        return redirect('terminal:shipping_lines')

    lines = ShippingLine.objects.annotate(
        container_count=Count('containers')
    ).order_by('name')
    return render(request, 'portal/shipping_lines.html', {
        'lines': lines,
    })

