import csv
import datetime
import re
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
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


def _dashboard_url(date: datetime.date) -> str:
    return f"{reverse('terminal:dashboard')}?date={date.isoformat()}"


def _safe_next(request, fallback: str) -> str:
    """Retour vers la page d'origine d'une action, si elle appartient bien au portail."""
    target = request.POST.get('next', '')
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return fallback


def default_work_date(user) -> datetime.date:
    """Liste sur laquelle travaille l'utilisateur : demain pour le superviseur soir, aujourd'hui sinon."""
    today = timezone.localdate()
    if getattr(user, 'is_evening_supervisor', False):
        return today + datetime.timedelta(days=1)
    return today


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
        .select_related('shipping_line', 'unavailability_reason')
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
    verification_filter = request.GET.get('verification', '').strip()

    base_qs = Container.objects.filter(scheduled_date=selected_date).select_related(
        'shipping_line', 'prefix_ref', 'unavailability_reason', 'created_by', 'updated_by',
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

    shipping_lines = ShippingLine.objects.filter(is_active=True)
    reasons = UnavailabilityReason.objects.filter(is_active=True)
    recent_logs = (
        ContainerStatusLog.objects.select_related('container', 'changed_by')
        .filter(container__scheduled_date=selected_date)
        .order_by('-changed_at')[:10]
    )

    # Conteneurs programmés mais pas encore vérifiés : proposés à chaque préparation de liste.
    backlog = None
    backlog_rows = []
    list_started = stats['total'] > 0
    if selected_date >= today:
        backlog_qs = unverified_backlog(selected_date)
        backlog = _backlog_breakdown(backlog_qs)
        if list_started:
            backlog_rows = list(backlog_qs.order_by('first_scheduled_date', 'container_number')[:60])

    return render(request, 'portal/dashboard.html', {
        'verification_filter': verification_filter,
        'show_verification': show_verification,
        'backlog': backlog,
        'backlog_rows': backlog_rows,
        'list_started': list_started,
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
        if status != Container.Status.PENDING:
            carried.status = status
            carried.unavailability_reason_id = int(reason_id) if (reason_id and reason_id.isdigit()) else None
            carried.unavailability_note = note
            carried.save(acting_user=request.user)
        messages.info(
            request,
            f"{normalized} était déjà programmé depuis le "
            f"{carried.first_scheduled_date.strftime('%d/%m/%Y')} : il a été repris dans la liste, "
            f"sans nouvelle saisie (ligne maritime conservée)."
        )
        return redirect(f"/admin/?date={target_date.isoformat()}")

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
            carried = _carry_into(prior, target_date, request.user)
            if default_status != Container.Status.PENDING:
                carried.status = default_status
                carried.save(acting_user=request.user)
            carried_numbers.append(normalized)
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
def carryover_view(request):
    """Suivi de tous les conteneurs programmés mais pas encore vérifiés, et décision de reprise."""
    if not request.user.is_staff_member:
        return redirect('terminal:home')

    today = timezone.localdate()
    target_date = default_carry_target(request.user)
    if not request.user.is_morning_supervisor:
        target_date = max(_parse_date(request.GET.get('date', ''), target_date), today)
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
    rows = list(rows.order_by('first_scheduled_date', 'scheduled_date', 'container_number'))

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
        'oldest': rows[0] if rows else None,
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
    if request.user.is_morning_supervisor:
        target_date = today
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
            'Periode', 'Programmes', 'Confirmes', 'Verifies', 'Non verifies',
            'Confirmes non marques', 'Repris', 'Rejetes', 'Taux de verification (%)',
        ])
        for b in buckets + [{'label': 'TOTAL', **totals}]:
            writer.writerow([
                b['label'], b['scheduled'], b['confirmed'], b['verified'], b['not_verified'],
                b['to_verify'], b['carried'], b['rejected'], '' if b['rate'] is None else b['rate'],
            ])
        return response

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

