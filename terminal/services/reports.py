"""Agrégats et helpers réutilisables pour les statistiques et rapports."""

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


def _reason_breakdown(qs, filter_kwargs: dict, reason_field: str) -> list:
    """Agrège et calcule les pourcentages pour une répartition par motif."""
    raw = list(
        qs.filter(**filter_kwargs)
        .values(reason_field)
        .annotate(total=Count('id'))
        .order_by('-total')
    )
    peak = max((r['total'] for r in raw), default=0)
    return [
        {
            'label': r[reason_field] or 'Non renseigné',
            'total': r['total'],
            'pct': round(100 * r['total'] / peak) if peak else 0,
        }
        for r in raw
    ]


