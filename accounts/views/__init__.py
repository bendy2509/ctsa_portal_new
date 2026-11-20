"""Paquet des vues accounts (ré-export complet pour compatibilité URLs et tests)."""

from .auth import login_view, logout_view, register_view
from .profile import profile_view
from .users import (
    _log_user_audit,
    user_create_view,
    user_delete_view,
    user_edit_view,
    user_list_view,
)

__all__ = [
    '_log_user_audit',
    'login_view',
    'logout_view',
    'profile_view',
    'register_view',
    'user_create_view',
    'user_delete_view',
    'user_edit_view',
    'user_list_view',
]
