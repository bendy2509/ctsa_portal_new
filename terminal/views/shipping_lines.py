"""Gestion des lignes maritimes."""

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


@require_permission(Actions.SHIPPING_LINE_MANAGE)
def shipping_lines_view(request):
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
    return render(request, 'portal/parametres/shipping_lines.html', {
        'lines': lines,
    })


@require_permission(Actions.SHIPPING_LINE_MANAGE)
def shipping_line_create_view(request):
    """Page dédiée de création d'une ligne maritime."""
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

    return render(request, 'portal/parametres/shipping_line_form.html', {
        'line': None,
        'name': name,
        'error': error,
    })


@require_permission(Actions.SHIPPING_LINE_MANAGE)
def shipping_line_edit_view(request, pk):
    """Page dédiée de modification d'une ligne maritime."""
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

    return render(request, 'portal/parametres/shipping_line_form.html', {
        'line': line,
        'name': name,
        'error': error,
    })


@require_permission(Actions.SHIPPING_LINE_MANAGE)
@require_POST
def shipping_line_delete_view(request, pk):
    line = get_object_or_404(ShippingLine, pk=pk)
    name = line.name
    line.delete()
    messages.info(request, f"Ligne maritime « {name} » supprimée.")
    return redirect('terminal:shipping_lines')

