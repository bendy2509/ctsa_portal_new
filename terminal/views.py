import csv
import datetime
import hashlib
import re
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.utils import safe_next_url
from .models import (
    ShippingLine,
    ContainerPrefix,
    Container,
    ContainerStatusLog,
    SavedContainer,
    SiteSettings,
    normalize_container_number,
    CONTAINER_NUMBER_REGEX,
)

# Create your views here.


def _parse_date(date_str: str, default_date: datetime.date) -> datetime.date:
    if not date_str:
        return default_date
    raw = date_str.strip()
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        pass
    try:
        return datetime.datetime.strptime(raw, '%d/%m/%Y').date()
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


PAGE_SIZE = 15


def _paginate(request, queryset):
    """Découpe un queryset en pages et renvoie (page, filtres actifs encodés sans `page`)."""
    page = Paginator(queryset, PAGE_SIZE).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    return page, params.urlencode()


def _dashboard_url(date: datetime.date) -> str:
    """Liste des conteneurs d'une date (cible des redirections après une action)."""
    return f"{reverse('terminal:containers')}?date={date.isoformat()}"


def _safe_next(request, fallback: str) -> str:
    """Retour vers la page d'origine d'une action, si elle appartient bien au portail."""
    return safe_next_url(request, request.POST.get('next', ''), fallback)


def default_work_date(user) -> datetime.date:
    """Liste par défaut : aujourd'hui pour l'administrateur et le superviseur."""
    return timezone.localdate()


def unverified_backlog(target_date: datetime.date):
    """Conteneurs programmés mais pas encore vérifiés, à proposer pour la liste `target_date`.

    Tout statut confondu (En attente, Indisponible, Confirmé) : un conteneur reste « à suivre »
    tant qu'il n'est ni vérifié, ni repris dans une liste ultérieure, ni rejeté. On ne retient que
    les séances antérieures à la liste préparée et déjà commencées.
    """
    limit = min(target_date, timezone.localdate() + datetime.timedelta(days=1))
    return (
        Container.objects.filter(scheduled_date__lt=limit, carry_decision='')
        .exclude(verification_result=Container.Verification.VERIFIED)
        .select_related('shipping_line')
    )


def default_carry_target(user) -> datetime.date:
    """Liste que la page « Non vérifiés » et le compteur de la sidebar visent par défaut.

    C'est la liste de travail de l'utilisateur (demain pour le superviseur soir, aujourd'hui
    sinon) : la séance en cours n'est donc comptée « non vérifiée » que pour celui qui
    prépare déjà la liste du lendemain. Même périmètre que l'encart du tableau de bord.
    """
    return default_work_date(user)


def _carry_into(old: Container, target_date: datetime.date, user) -> Container:
    """Reprend un conteneur non vérifié dans la liste `target_date`, sans le ressaisir.

    Chemin unique utilisé par le bouton en un clic, les actions groupées et le garde-fou des
    formulaires d'inscription : ligne maritime et première programmation sont conservées.
    """
    new = Container.objects.filter(
        container_number=old.container_number, scheduled_date=target_date
    ).first()
    first_date = old.first_scheduled_date or old.scheduled_date
    if new is None:
        new = Container(
            container_number=old.container_number,
            scheduled_date=target_date,
            status=Container.Status.PENDING,
            shipping_line=old.shipping_line,
            prefix_ref=old.prefix_ref,
            carried_from=old,
            first_scheduled_date=first_date,
        )
        new.save(acting_user=user)
    else:
        Container.objects.filter(pk=new.pk).update(carried_from=old, first_scheduled_date=first_date)
        new.carried_from = old
        new.first_scheduled_date = first_date

    _close_tracking(
        old,
        Container.CarryDecision.CARRIED,
        user,
        f"Repris dans la liste du {target_date.strftime('%d/%m/%Y')}",
    )
    return new


def _close_tracking(old: Container, decision: str, user, reason: str):
    """Sort un conteneur du suivi des non vérifiés (repris ou rejeté) et trace l'opération."""
    # update() : on ne touche ni à « Mis à jour le » ni au statut de l'ancienne séance.
    Container.objects.filter(pk=old.pk).update(
        carry_decision=decision,
        carry_decided_by=user,
        carry_decided_at=timezone.now(),
    )
    old.carry_decision = decision
    ContainerStatusLog.objects.create(
        container=old,
        action=decision,
        old_status=old.status,
        new_status=old.status,
        reason_text=reason,
        changed_by=user,
    )


def _backlog_breakdown(queryset) -> dict:
    counts = {row['status']: row['n'] for row in queryset.values('status').annotate(n=Count('id'))}
    return {
        'total': sum(counts.values()),
        'confirmed': counts.get(Container.Status.CONFIRMED, 0),
        'pending': counts.get(Container.Status.PENDING, 0),
        'unavailable': counts.get(Container.Status.UNAVAILABLE, 0),
    }


def home_view(request):
    today = timezone.localdate()
    shipping_lines = ShippingLine.objects.filter(is_active=True).order_by('name')
    return render(request, 'public/home.html', {
        'today': today,
        'shipping_lines': shipping_lines,
    })


def _relevant_schedules(numbers, today: datetime.date) -> dict:
    """Pour chaque numéro, la programmation à montrer au client (voir _pick_relevant_schedule)."""
    grouped = {}
    for c in (
        Container.objects.select_related('shipping_line')
        .filter(container_number__in=list(numbers))
        .order_by('container_number', '-scheduled_date', '-updated_at')
    ):
        grouped.setdefault(c.container_number, []).append(c)
    return {num: _pick_relevant_schedule(items, today) for num, items in grouped.items()}


def _state_signature(numbers, schedules: dict, today: datetime.date) -> str:
    """Empreinte de ce que voit le client : elle change dès qu'un statut, une vérification ou une
    programmation change. Elle ne contient aucune donnée lisible : un numéro inconnu et un numéro
    connu donnent tous deux une chaîne opaque."""
    parts = [today.isoformat()]
    for number in sorted(numbers):
        c = schedules.get(number)
        if c is None:
            parts.append(f'{number}:-')
        else:
            parts.append(
                f'{number}:{c.pk}:{c.status}:{c.verification_result}:{c.updated_at.isoformat()}'
            )
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:20]


def verification_view(request):
    raw_query = request.GET.get('q', '').strip()
    normalized = normalize_container_number(raw_query)
    today = timezone.localdate()

    result_today = None
    format_warning = None
    is_saved = False
    auto_refresh = False
    refresh_signature = ''

    if normalized:
        if not CONTAINER_NUMBER_REGEX.match(normalized):
            format_warning = (
                "Le numéro de conteneur doit comporter exactement 4 lettres suivies de 7 chiffres (exemple : CMAU4052450)."
            )
        else:
            schedules = _relevant_schedules([normalized], today)
            result_today = schedules.get(normalized)
            # Tant que le conteneur n'est pas vérifié, la fiche peut encore changer : elle se
            # met à jour toute seule (voir verification_state_view et ctsa.js).
            auto_refresh = not (result_today and result_today.is_verified)
            refresh_signature = _state_signature([normalized], schedules, today)

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
        'auto_refresh': auto_refresh,
        'refresh_signature': refresh_signature,
    })


def verification_state_view(request):
    """Empreinte de l'état d'un conteneur, interrogée par la fiche publique pour s'actualiser."""
    normalized = normalize_container_number(request.GET.get('q', ''))
    if not CONTAINER_NUMBER_REGEX.match(normalized):
        return JsonResponse({'signature': ''})
    today = timezone.localdate()
    schedules = _relevant_schedules([normalized], today)
    response = JsonResponse({'signature': _state_signature([normalized], schedules, today)})
    response['Cache-Control'] = 'no-store'
    return response


@login_required
def client_watchlist_view(request):
    today = timezone.localdate()
    if request.method == 'POST':
        raw_num = request.POST.get('container_number', '')
        label = request.POST.get('label', '').strip()
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
                # notify_whatsapp n'est pas touché : aucun écran ne propose encore d'alertes.
                defaults={'label': label},
            )
            if created:
                messages.success(request, f"Le conteneur {normalized} a été ajouté à votre suivi.")
            else:
                messages.info(request, f"Le suivi du conteneur {normalized} a été mis à jour.")
        return redirect('terminal:client_watchlist')

    saved_list = list(SavedContainer.objects.filter(user=request.user))
    numbers = [s.container_number for s in saved_list]

    containers_by_num = _relevant_schedules(numbers, today) if numbers else {}

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
        'refresh_signature': _state_signature(numbers, containers_by_num, today),
    })


@login_required
def client_watchlist_state_view(request):
    """Empreinte de l'état des conteneurs suivis par le client connecté."""
    today = timezone.localdate()
    numbers = list(
        SavedContainer.objects.filter(user=request.user).values_list('container_number', flat=True)
    )
    schedules = _relevant_schedules(numbers, today) if numbers else {}
    response = JsonResponse({'signature': _state_signature(numbers, schedules, today)})
    response['Cache-Control'] = 'no-store'
    return response


@login_required
@require_POST
def client_watchlist_remove_view(request, pk):
    saved = get_object_or_404(SavedContainer, pk=pk, user=request.user)
    num = saved.container_number
    saved.delete()
    messages.info(request, f"Le conteneur {num} a été retiré de votre liste de suivi.")
    return redirect('terminal:client_watchlist')


@login_required
def prepare_view(request):
    """Préparer une liste : inscrire des conteneurs et reprendre les non vérifiés, à l'écart du
    tableau de bord, qui ne montre plus que la liste elle-même."""
    if not request.user.is_staff_member:
        return redirect('terminal:home')

    today = timezone.localdate()
    tomorrow = today + datetime.timedelta(days=1)
    selected_date = max(_parse_date(request.GET.get('date', ''), default_work_date(request.user)), today)

    session_qs = Container.objects.filter(scheduled_date=selected_date).select_related('shipping_line')
    registered_count = session_qs.count()
    backlog_qs = unverified_backlog(selected_date)

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
    })


@login_required
def dashboard_view(request):
    """Tableau de bord : résumé de la séance, indicateurs, points d'attention et activité récente."""
    if not request.user.is_staff_member:
        return redirect('terminal:home')

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


@login_required
def container_list_view(request):
    if not request.user.is_staff_member:
        return redirect('terminal:client_watchlist')

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


@login_required
@require_POST
def container_add_single_view(request):
    if not request.user.is_staff_member:
        return redirect('terminal:home')

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
        messages.error(
            request,
            f"Numéro « {raw_number} » invalide : il doit comporter 4 lettres et 7 chiffres (ex. CMAU4052450)."
        )
        return redirect(_safe_next(request, _dashboard_url(target_date)))

    # Ligne maritime obligatoire dès qu'au moins une ligne active existe.
    line_id = int(shipping_line_id) if shipping_line_id.isdigit() else None
    if line_id is None and ShippingLine.objects.filter(is_active=True).exists():
        messages.error(request, "Veuillez sélectionner une ligne maritime.")
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


@login_required
@require_POST
def container_add_bulk_view(request):
    if not request.user.is_staff_member:
        return redirect('terminal:home')

    today = timezone.localdate()
    default_date = today

    raw_list = request.POST.get('container_list', '')
    target_date = _parse_date(request.POST.get('scheduled_date', ''), default_date)
    shipping_line_id = request.POST.get('shipping_line') or ''

    # Tout nouvel ajout dans une liste démarre « En attente » jusqu'à confirmation sur la rampe.
    default_status = Container.Status.PENDING
    resolved_line_id = int(shipping_line_id) if shipping_line_id.isdigit() else None
    if resolved_line_id is None and ShippingLine.objects.filter(is_active=True).exists():
        messages.error(request, "Veuillez sélectionner une ligne maritime.")
        return redirect(_safe_next(request, _dashboard_url(target_date)))

    tokens = [t.strip() for t in re.split(r'[\r\n,;]+', raw_list) if t.strip()]
    if not tokens:
        messages.warning(request, "Aucun numéro de conteneur fourni dans la zone d'ajout massif.")
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


@login_required
@require_POST
def container_update_status_view(request, pk):
    if not request.user.can_edit_or_delete_containers:
        messages.error(
            request,
            "Action réservée aux administrateurs et superviseurs."
        )
        return redirect('terminal:dashboard')

    container = get_object_or_404(Container, pk=pk)
    new_status = request.POST.get('status', container.status)
    note = request.POST.get('unavailability_note', '').strip()
    shipping_line_id = request.POST.get('shipping_line') or None

    if new_status in dict(Container.Status.choices):
        container.status = new_status
        if shipping_line_id and shipping_line_id.isdigit():
            container.shipping_line_id = int(shipping_line_id)
        if new_status == Container.Status.UNAVAILABLE:
            if 'unavailability_note' in request.POST:
                container.unavailability_note = note
        else:
            container.unavailability_note = ''
        container.save(acting_user=request.user)
        messages.success(
            request,
            f"Conteneur {container.container_number} mis à jour ({container.get_status_display()}).",
        )

    return redirect(_safe_next(request, _dashboard_url(container.scheduled_date)))


@login_required
@require_POST
def container_status_bulk_view(request):
    """Change le statut (Confirmé, En attente, Indisponible) des conteneurs cochés ou de tous les conteneurs en attente."""
    if not request.user.can_edit_or_delete_containers:
        messages.error(
            request,
            "Action réservée aux administrateurs et superviseurs.",
        )
        return redirect('terminal:dashboard')

    today = timezone.localdate()
    target_date = _parse_date(request.POST.get('date', ''), today)
    new_status = request.POST.get('status', '').strip()
    note = request.POST.get('unavailability_note', '').strip()
    fallback = _dashboard_url(target_date)

    if new_status not in dict(Container.Status.choices):
        messages.error(request, "Statut inconnu.")
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
    for container in qs:
        container.status = new_status
        if new_status == Container.Status.UNAVAILABLE:
            container.unavailability_note = note
        else:
            container.unavailability_note = ''
        container.save(acting_user=request.user)
        updated_count += 1

    label = dict(Container.Status.choices)[new_status]
    if updated_count:
        messages.success(
            request,
            f"{updated_count} conteneur(s) passé(s) au statut « {label} » pour la séance du {target_date.strftime('%d/%m/Y').replace('/Y', '/' + target_date.strftime('%Y'))}.",
        )
    else:
        messages.warning(request, "Aucun conteneur à modifier pour cette sélection.")
    return redirect(_safe_next(request, fallback))


@login_required
@require_POST
def container_delete_view(request, pk):
    if not request.user.can_edit_or_delete_containers:
        messages.error(request, "Vous n'avez pas l'autorisation de supprimer un conteneur.")
        return redirect('terminal:dashboard')

    container = get_object_or_404(Container, pk=pk)
    scheduled = container.scheduled_date
    num = container.container_number
    # Si ce conteneur avait été repris d'une séance précédente, celle-ci redevient « à suivre » :
    # sans cela, le conteneur disparaîtrait du suivi des non vérifiés sans avoir été vérifié.
    if container.carried_from_id:
        Container.objects.filter(
            pk=container.carried_from_id, carry_decision=Container.CarryDecision.CARRIED
        ).update(carry_decision='', carry_decided_by=None, carry_decided_at=None)
    container.delete()
    messages.info(request, f"Le conteneur {num} a été supprimé de la liste du {scheduled.strftime('%d/%m/%Y')}.")
    return redirect(f"{reverse('terminal:containers')}?date={scheduled.isoformat()}")


@login_required
def container_detail_view(request, pk):
    """Fiche détail d'un conteneur programmé : infos, changement de statut, suppression, historique."""
    if not request.user.is_staff_member:
        return redirect('terminal:client_watchlist')

    container = get_object_or_404(
        Container.objects.select_related(
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


@login_required
def container_edit_view(request, pk):
    """Page dédiée de modification d'un conteneur programmé."""
    if not request.user.can_edit_or_delete_containers:
        messages.error(request, "Vous n'avez pas l'autorisation de modifier ce conteneur.")
        return redirect('terminal:container_detail', pk=pk)

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

        if not errors:
            container.container_number = normalized
            container.shipping_line = line_obj
            container.scheduled_date = parsed_date
            container.status = new_status
            container.unavailability_note = note if new_status == Container.Status.UNAVAILABLE else ''
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


@login_required
@require_POST
def container_set_verification_view(request, pk):
    """Marque un conteneur confirmé Vérifié / Non vérifié (ou annule le marquage)."""
    if not request.user.can_mark_verification:
        return redirect('terminal:home')

    container = get_object_or_404(Container, pk=pk)
    result = request.POST.get('result', '').strip()
    note = request.POST.get('verification_note', '').strip()[:255]
    try:
        container.set_verification(result, acting_user=request.user, note=note)
    except ValidationError as exc:
        messages.error(request, f"{container.container_number} : {exc.messages[0]}")
    else:
        label = container.get_verification_result_display() if result else "marquage annulé"
        messages.success(request, f"Conteneur {container.container_number} : {label}.")
    return redirect(_safe_next(request, _dashboard_url(container.scheduled_date)))


@login_required
@require_POST
def container_verify_bulk_view(request):
    """Clôture de séance : applique un résultat aux conteneurs cochés ou aux confirmés restants."""
    if not request.user.can_mark_verification:
        return redirect('terminal:home')

    today = timezone.localdate()
    target_date = _parse_date(request.POST.get('date', ''), today)
    result = request.POST.get('result', '').strip()
    fallback = _dashboard_url(target_date)

    if result not in Container.Verification.values:
        messages.error(request, "Résultat de vérification inconnu.")
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
    for container in qs:
        try:
            container.set_verification(result, acting_user=request.user)
            done += 1
        except ValidationError:
            continue

    label = dict(Container.Verification.choices)[result].lower()
    if done:
        messages.success(
            request,
            f"{done} conteneur(s) marqué(s) « {label} » pour la séance du {target_date.strftime('%d/%m/%Y')}."
        )
    else:
        messages.warning(request, "Aucun conteneur confirmé à marquer pour cette séance.")
    return redirect(_safe_next(request, fallback))


@login_required
def inspection_view(request):
    """Onglet Inspection : marquer Vérifié / Non vérifié les conteneurs confirmés d'une séance."""
    if not request.user.is_staff_member:
        return redirect('terminal:home')

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

    return render(request, 'portal/inspection.html', {
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


@login_required
def register_view(request):
    """Registre des vérifications : qui a été vérifié, quand, et les quantités sur un intervalle."""
    if not request.user.is_staff_member:
        return redirect('terminal:home')

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


@login_required
def carryover_view(request):
    """Suivi de tous les conteneurs programmés mais pas encore vérifiés, et décision de reprise."""
    if not request.user.is_staff_member:
        return redirect('terminal:home')

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


@login_required
@require_POST
def carryover_decide_view(request):
    """Reprend (bouton en un clic ou sélection) ou rejette des conteneurs non vérifiés."""
    if not request.user.is_staff_member:
        return redirect('terminal:home')

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


def _period_bounds(kind: str, ref: datetime.date):
    """Bornes (incluses) de la semaine (lundi-dimanche) ou du mois contenant `ref`."""
    if kind == 'mois':
        start = ref.replace(day=1)
        next_month = (start + datetime.timedelta(days=32)).replace(day=1)
        return start, next_month - datetime.timedelta(days=1)
    start = ref - datetime.timedelta(days=ref.weekday())
    return start, start + datetime.timedelta(days=6)


def _rate(part: int, whole: int):
    return round(100 * part / whole) if whole else None


_STAT_KEYS = ('scheduled', 'confirmed', 'verified', 'not_verified', 'to_verify', 'carried', 'rejected')


def _stat_annotations():
    V, S, D = Container.Verification, Container.Status, Container.CarryDecision
    return {
        'scheduled': Count('id'),
        'confirmed': Count('id', filter=Q(status=S.CONFIRMED)),
        'verified': Count('id', filter=Q(verification_result=V.VERIFIED)),
        'not_verified': Count('id', filter=Q(verification_result=V.NOT_VERIFIED)),
        'to_verify': Count('id', filter=Q(status=S.CONFIRMED, verification_result='')),
        'carried': Count('id', filter=Q(carry_decision=D.CARRIED)),
        'rejected': Count('id', filter=Q(carry_decision=D.REJECTED)),
    }


@login_required
def statistics_view(request):
    if not request.user.is_staff_member:
        return redirect('terminal:home')

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
            'Periode', 'Programmes', 'Confirmes', 'Verifies', 'Restent a verifier',
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
    })


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


@login_required
def container_export_csv_view(request):
    if not request.user.is_staff_member:
        return redirect('terminal:home')

    today = timezone.localdate()
    selected_date = _parse_date(request.GET.get('date', ''), today)
    status_filter = request.GET.get('status', '').strip()

    qs = Container.objects.filter(scheduled_date=selected_date).select_related(
        'shipping_line', 'prefix_ref', 'updated_by'
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
        'Verification',
        'Premiere Programmation',
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
            c.get_verification_result_display(),
            (c.first_scheduled_date or c.scheduled_date).strftime('%d/%m/%Y'),
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

    page, page_query = _paginate(request, logs)
    return render(request, 'portal/status_logs.html', {
        'logs': page.object_list,
        'page': page,
        'page_query': page_query,
        'q': q,
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


@login_required
def shipping_line_create_view(request):
    """Page dédiée de création d'une ligne maritime."""
    if not request.user.is_portal_admin:
        messages.error(request, "Accès réservé aux administrateurs.")
        return redirect('terminal:dashboard')

    error = ''
    name = ''
    if request.method == 'POST':
        from django.utils.text import slugify
        name = (request.POST.get('name') or '').strip()
        if not name:
            error = "Le nom de la ligne maritime est obligatoire."
        elif ShippingLine.objects.filter(name__iexact=name).exists():
            error = f"La ligne maritime « {name} » existe déjà."
        else:
            ShippingLine.objects.create(name=name, code=slugify(name)[:20].upper())
            messages.success(request, f"Ligne maritime « {name} » ajoutée.")
            return redirect('terminal:shipping_lines')

    return render(request, 'portal/shipping_line_form.html', {
        'line': None,
        'name': name,
        'error': error,
    })


@login_required
def shipping_line_edit_view(request, pk):
    """Page dédiée de modification d'une ligne maritime."""
    if not request.user.is_portal_admin:
        messages.error(request, "Accès réservé aux administrateurs.")
        return redirect('terminal:dashboard')

    line = get_object_or_404(ShippingLine, pk=pk)
    error = ''
    name = line.name
    if request.method == 'POST':
        from django.utils.text import slugify
        name = (request.POST.get('name') or '').strip()
        if not name:
            error = "Le nom de la ligne maritime est obligatoire."
        elif ShippingLine.objects.filter(name__iexact=name).exclude(pk=line.pk).exists():
            error = f"Une ligne maritime nommée « {name} » existe déjà."
        else:
            line.name = name
            line.code = slugify(name)[:20].upper()
            line.save()
            messages.success(request, f"Ligne maritime renommée en « {name} ».")
            return redirect('terminal:shipping_lines')

    return render(request, 'portal/shipping_line_form.html', {
        'line': line,
        'name': name,
        'error': error,
    })


@login_required
@require_POST
def shipping_line_delete_view(request, pk):
    if not request.user.is_portal_admin:
        messages.error(request, "Accès réservé aux administrateurs.")
        return redirect('terminal:dashboard')

    line = get_object_or_404(ShippingLine, pk=pk)
    name = line.name
    line.delete()
    messages.info(request, f"Ligne maritime « {name} » supprimée.")
    return redirect('terminal:shipping_lines')


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
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['hero_badge'].required = False
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')


@login_required
def site_settings_view(request):
    """Espace d'édition des textes et coordonnées publiques du portail."""
    if not request.user.is_portal_admin:
        messages.error(request, "Accès réservé aux administrateurs.")
        return redirect('terminal:dashboard')

    instance = SiteSettings.get_solo()
    if request.method == 'POST':
        form = SiteSettingsForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, "Les informations et textes publics ont été mis à jour.")
            return redirect('terminal:site_settings')
        messages.error(request, "Veuillez corriger les champs signalés ci-dessous.")
    else:
        form = SiteSettingsForm(instance=instance)

    return render(request, 'portal/site_settings.html', {
        'form': form,
        'site_obj': instance,
    })


def pwa_manifest_view(request):
    """Manifeste Web App (PWA) pour l'installation mobile du Portail RAMPE."""
    from django.templatetags.static import static
    site_obj = SiteSettings.get_solo()
    manifest = {
        'name': f"{site_obj.company_name} — {site_obj.portal_title}",
        'short_name': 'CTSA RAMPE',
        'description': site_obj.portal_subtitle,
        'start_url': '/',
        'scope': '/',
        'display': 'standalone',
        'orientation': 'portrait',
        'background_color': '#222b35',
        'theme_color': '#222b35',
        'lang': 'fr',
        'icons': [
            {
                'src': static('img/icon-192.png'),
                'sizes': '192x192',
                'type': 'image/png',
                'purpose': 'any',
            },
            {
                'src': static('img/icon-512.png'),
                'sizes': '512x512',
                'type': 'image/png',
                'purpose': 'any',
            },
            {
                'src': static('img/icon-512-maskable.png'),
                'sizes': '512x512',
                'type': 'image/png',
                'purpose': 'maskable',
            },
        ],
    }
    return JsonResponse(manifest, content_type='application/manifest+json; charset=utf-8')


def pwa_service_worker_view(request):
    """Service Worker PWA servi à la racine pour couvrir tout le site."""
    return render(request, 'pwa/sw.js', content_type='application/javascript; charset=utf-8')



