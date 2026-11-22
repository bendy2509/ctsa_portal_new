"""Enregistrement des arrivées sans liste."""

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


@require_permission(Actions.CONTAINER_ARRIVAL_CREATE)
def arrival_create_view(request):
    """Page dédiée d'enregistrement d'une arrivée sans liste programmée."""
    shipping_lines = ShippingLine.objects.filter(is_active=True).order_by('name')
    errors = {}
    duplicate_container = None
    iso_warning = False
    raw_number = ''
    raw_line = ''

    if request.method == 'POST':
        raw_number = request.POST.get('container_number', '').strip()
        raw_line = request.POST.get('shipping_line', '').strip()
        confirm_iso = request.POST.get('confirm_iso') == '1'
        normalized = normalize_container_number(raw_number)

        line_obj = None
        if raw_line:
            if raw_line.isdigit():
                line_obj = ShippingLine.objects.filter(pk=int(raw_line), is_active=True).first()
            if line_obj is None:
                errors['shipping_line'] = "Ligne maritime invalide."

        if not ARRIVAL_NUMBER_REGEX.match(normalized):
            errors['container_number'] = (
                "Format requis : 3 lettres suivies de U, J ou Z puis 7 chiffres (ex. CMAU4052450)."
            )
        else:
            duplicate_container = (
                Container.all_objects.filter(
                    normalized_number=normalized,
                    archived_at__isnull=True,
                )
                .order_by('-scheduled_date', '-arrived_at', '-pk')
                .first()
            )
            if duplicate_container is not None:
                errors['container_number'] = (
                    f"Le conteneur {normalized} existe déjà dans le système "
                    f"({duplicate_container.get_status_display()})."
                )
            elif not is_valid_iso6346(normalized) and not confirm_iso:
                iso_warning = True

        if not errors and not iso_warning:
            container = Container(
                container_number=normalized,
                shipping_line=line_obj,
                origin=Container.Origin.ARRIVE_SANS_LISTE,
                status=Container.Status.REGISTERED,
                scheduled_date=None,
                arrived_at=timezone.now(),
            )
            container.save(acting_user=request.user)
            messages.success(
                request,
                f"Arrivée du conteneur {container.container_number} enregistrée (hors liste).",
            )
            return redirect('terminal:container_detail', pk=container.pk)

    recent_arrivals = list(
        Container.registered_objects.select_related('shipping_line')
        .order_by('-arrived_at', '-pk')[:10]
    )

    return render(request, 'portal/conteneurs/arrival_create.html', {
        'shipping_lines': shipping_lines,
        'errors': errors,
        'duplicate_container': duplicate_container,
        'iso_warning': iso_warning,
        'raw_number': raw_number,
        'raw_line': raw_line,
        'now_preview': timezone.localtime(timezone.now()),
        'recent_arrivals': recent_arrivals,
        'registered_pool_count': Container.registered_objects.count(),
    })


