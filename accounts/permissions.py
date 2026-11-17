"""Module centralisé de gestion des permissions (liste blanche avec refus par défaut)."""

import datetime
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from accounts.models import User


class Actions:
    PORTAL_DASHBOARD = 'portal.dashboard'
    CONTAINER_LIST = 'container.list'
    CONTAINER_DETAIL = 'container.detail'
    CONTAINER_PREPARE = 'container.prepare'
    CONTAINER_CHANGE_STATUS = 'container.change_status'
    CONTAINER_ARRIVAL_CREATE = 'container.arrival_create'
    CONTAINER_ARCHIVE = 'container.archive'
    CONTAINER_RESTORE = 'container.restore'
    CONTAINER_VERIFY = 'container.verify'
    CONTAINER_VERIFY_CORRECT = 'container.verify_correct'
    CONTAINER_VERIFY_DELETE_MOVEMENT = 'container.verify_delete_movement'
    CONTAINER_CARRYOVER = 'container.carryover'
    REPORT_VIEW = 'report.view'
    SHIPPING_LINE_MANAGE = 'shipping_line.manage'
    USER_MANAGE = 'user.manage'
    USER_MANAGE_AGENT_SUPERVISOR = 'user.manage_agent_supervisor'
    USER_MANAGE_ADMIN = 'user.manage_admin'
    USER_MANAGE_SUPERADMIN = 'user.manage_superadmin'
    SITE_SETTINGS_MANAGE = 'site_settings.manage'
    MOTIF_MANAGE = 'motif.manage'
    CLIENT_WATCHLIST = 'client.watchlist'


# Refus par défaut pour TOUS les rôles (hors Superadmin is_superuser=True).
ROLE_ALLOWLIST = {
    User.Role.AGENT: frozenset({
        Actions.PORTAL_DASHBOARD,
        Actions.CONTAINER_LIST,
        Actions.CONTAINER_DETAIL,
        Actions.CONTAINER_VERIFY,
        Actions.CONTAINER_VERIFY_CORRECT,
        Actions.CLIENT_WATCHLIST,
    }),
    User.Role.SUPERVISOR: frozenset({
        Actions.PORTAL_DASHBOARD,
        Actions.CONTAINER_LIST,
        Actions.CONTAINER_DETAIL,
        Actions.CONTAINER_PREPARE,
        Actions.CONTAINER_CHANGE_STATUS,
        Actions.CONTAINER_ARRIVAL_CREATE,
        Actions.CONTAINER_ARCHIVE,
        Actions.CONTAINER_VERIFY,
        Actions.CONTAINER_VERIFY_CORRECT,
        Actions.CONTAINER_VERIFY_DELETE_MOVEMENT,
        Actions.CONTAINER_CARRYOVER,
        Actions.CLIENT_WATCHLIST,
    }),
    User.Role.ADMIN: frozenset({
        Actions.PORTAL_DASHBOARD,
        Actions.CONTAINER_LIST,
        Actions.CONTAINER_DETAIL,
        Actions.CONTAINER_PREPARE,
        Actions.CONTAINER_CHANGE_STATUS,
        Actions.CONTAINER_ARRIVAL_CREATE,
        Actions.CONTAINER_ARCHIVE,
        Actions.CONTAINER_VERIFY,
        Actions.CONTAINER_VERIFY_CORRECT,
        Actions.CONTAINER_VERIFY_DELETE_MOVEMENT,
        Actions.CONTAINER_CARRYOVER,
        Actions.REPORT_VIEW,
        Actions.SHIPPING_LINE_MANAGE,
        Actions.USER_MANAGE,
        Actions.USER_MANAGE_AGENT_SUPERVISOR,
        Actions.USER_MANAGE_ADMIN,
        Actions.CLIENT_WATCHLIST,
    }),
    User.Role.CLIENT: frozenset({
        Actions.CLIENT_WATCHLIST,
    }),
}


# Correspondance URL nommée -> action requise (audit complet des routes).
URL_PERMISSIONS = {
    'terminal:dashboard': Actions.PORTAL_DASHBOARD,
    'terminal:containers': Actions.CONTAINER_LIST,
    'terminal:container_detail': Actions.CONTAINER_DETAIL,
    'terminal:container_edit': Actions.CONTAINER_CHANGE_STATUS,
    'terminal:prepare': Actions.CONTAINER_PREPARE,
    'terminal:container_add_single': Actions.CONTAINER_PREPARE,
    'terminal:container_add_bulk': Actions.CONTAINER_PREPARE,
    'terminal:container_update_status': Actions.CONTAINER_CHANGE_STATUS,
    'terminal:container_status_bulk': Actions.CONTAINER_CHANGE_STATUS,
    'terminal:container_delete': Actions.CONTAINER_ARCHIVE,
    'terminal:container_set_verification': Actions.CONTAINER_VERIFY,
    'terminal:container_verify_bulk': Actions.CONTAINER_VERIFY,
    'terminal:inspection': Actions.CONTAINER_VERIFY,
    'terminal:register': Actions.REPORT_VIEW,
    'terminal:carryover': Actions.CONTAINER_CARRYOVER,
    'terminal:carryover_decide': Actions.CONTAINER_CARRYOVER,
    'terminal:statistics': Actions.REPORT_VIEW,
    'terminal:container_export_pdf': Actions.REPORT_VIEW,
    'terminal:container_export_csv': Actions.REPORT_VIEW,
    'terminal:status_logs': Actions.REPORT_VIEW,
    'terminal:shipping_lines': Actions.SHIPPING_LINE_MANAGE,
    'terminal:shipping_line_create': Actions.SHIPPING_LINE_MANAGE,
    'terminal:shipping_line_edit': Actions.SHIPPING_LINE_MANAGE,
    'terminal:shipping_line_delete': Actions.SHIPPING_LINE_MANAGE,
    'terminal:site_settings': Actions.SITE_SETTINGS_MANAGE,
    'terminal:hero_slide_create': Actions.SITE_SETTINGS_MANAGE,
    'terminal:hero_slide_toggle': Actions.SITE_SETTINGS_MANAGE,
    'terminal:hero_slide_delete': Actions.SITE_SETTINGS_MANAGE,
    'terminal:client_watchlist': Actions.CLIENT_WATCHLIST,
    'terminal:client_watchlist_state': Actions.CLIENT_WATCHLIST,
    'terminal:client_watchlist_remove': Actions.CLIENT_WATCHLIST,
    'accounts:user_list': Actions.USER_MANAGE,
    'accounts:user_create': Actions.USER_MANAGE,
    'accounts:user_edit': Actions.USER_MANAGE,
    'accounts:user_delete': Actions.USER_MANAGE,
}


def _get_windows():
    from terminal.models import SiteSettings
    settings_obj = SiteSettings.get_solo()
    return (
        getattr(settings_obj, 'correction_window_minutes', 60) or 60,
        getattr(settings_obj, 'delete_window_hours', 24) or 24,
    )


def _ensure_aware(dt):
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def peut(utilisateur, action: str, objet=None) -> bool:
    """Point d'entrée unique de contrôle d'accès côté serveur et côté template."""
    if not utilisateur or not getattr(utilisateur, 'is_authenticated', False):
        return False
    if not getattr(utilisateur, 'is_active', False):
        return False

    # Superadmin (= Django is_superuser) : accès total garanti.
    if getattr(utilisateur, 'is_superuser', False):
        return True

    role = getattr(utilisateur, 'role', None)
    allowed = ROLE_ALLOWLIST.get(role, frozenset())
    if action not in allowed:
        return False

    if objet is None:
        return True

    if action == Actions.CONTAINER_ARCHIVE:
        if role == User.Role.SUPERVISOR:
            _, delete_hours = _get_windows()
            origin = getattr(objet, 'origin', 'programme')
            ref_dt = (
                getattr(objet, 'arrived_at', None)
                if origin == 'arrive_sans_liste' and getattr(objet, 'arrived_at', None)
                else getattr(objet, 'created_at', None)
            )
            ref_dt = _ensure_aware(ref_dt)
            if ref_dt is None:
                return False
            return (timezone.now() - ref_dt) < datetime.timedelta(hours=delete_hours)
        return True

    if action == Actions.CONTAINER_VERIFY_CORRECT:
        correction_minutes, _ = _get_windows()
        author_id = None
        marked_at = None
        is_historical = False

        if hasattr(objet, 'created_by_id') and hasattr(objet, 'created_at'):
            # Objet VerificationEvent
            author_id = objet.created_by_id
            marked_at = objet.created_at
            is_historical = getattr(objet, 'is_historical', False)
        else:
            # Objet Container : lire le dernier VerificationEvent non archivé s'il existe
            if hasattr(objet, 'verification_events'):
                latest_event = (
                    objet.verification_events.filter(archived_at__isnull=True)
                    .order_by('-created_at', '-pk')
                    .first()
                )
            else:
                latest_event = None
            if latest_event is not None:
                author_id = latest_event.created_by_id
                marked_at = latest_event.created_at
                is_historical = latest_event.is_historical
            else:
                author_id = getattr(objet, 'verified_by_id', None)
                marked_at = getattr(objet, 'verified_at', None)

        if is_historical or marked_at is None:
            return False
        marked_at = _ensure_aware(marked_at)
        if (timezone.now() - marked_at) >= datetime.timedelta(minutes=correction_minutes):
            return False
        if role == User.Role.AGENT:
            return author_id is not None and author_id == utilisateur.pk
        return True

    if action == Actions.USER_MANAGE:
        # Un Admin ne peut jamais toucher un compte Superadmin.
        if getattr(objet, 'is_superuser', False):
            return False
        return True

    return True


def render_403(request, message: str = "Vous n'avez pas l'autorisation d'accéder à cette page."):
    return render(
        request,
        'portal/403.html',
        {'error_message': message},
        status=403,
    )


def require_permission(action: str):
    """Décorateur de vue : redirige les anonymes vers la connexion, répond 403 sinon."""
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not peut(request.user, action):
                return render_403(request)
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator
