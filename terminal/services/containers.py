"""Helpers et requêtes réutilisables pour la gestion des conteneurs."""

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
            origin=old.origin,
            arrived_at=old.arrived_at,
            scheduled_date=target_date,
            status=Container.Status.PENDING,
            shipping_line=old.shipping_line,
            prefix_ref=old.prefix_ref,
            carried_from=old,
            first_scheduled_date=first_date,
        )
        new.save(acting_user=user)
    else:
        Container.objects.filter(pk=new.pk).update(
            carried_from=old,
            first_scheduled_date=first_date,
            origin=old.origin,
        )
        new.carried_from = old
        new.first_scheduled_date = first_date
        new.origin = old.origin

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




def _resolve_motif(motif_type: str, raw_reason_id, note: str):
    """Valide un motif structuré actif et sa précision obligatoire éventuelle."""
    reason_obj = None
    if raw_reason_id and str(raw_reason_id).isdigit():
        reason_obj = Motif.objects.filter(
            pk=int(raw_reason_id),
            type=motif_type,
            is_active=True,
        ).first()
    if reason_obj is None:
        raise ValidationError("Veuillez sélectionner un motif valide.")
    clean_note = (note or '').strip()[:255]
    if reason_obj.requires_note and not clean_note:
        raise ValidationError(f"Une précision est obligatoire pour le motif « {reason_obj.label} ».")
    return reason_obj, clean_note


