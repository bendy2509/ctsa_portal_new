"""Vues publiques et PWA (hors périmètre de modification)."""

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


def home_view(request):
    today = timezone.localdate()
    shipping_lines = ShippingLine.objects.filter(is_active=True).order_by('name')
    site_obj = SiteSettings.get_solo()
    hero_slides = (
        list(HeroSlide.objects.filter(is_active=True)[: site_obj.hero_carousel_limit])
        if site_obj.hero_carousel_enabled
        else []
    )
    return render(request, 'public/home.html', {
        'today': today,
        'shipping_lines': shipping_lines,
        'hero_slides': hero_slides,
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
            format_warning = _(
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



