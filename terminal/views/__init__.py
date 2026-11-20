"""Paquet des vues du terminal (ré-export complet pour compatibilité URLs et tests)."""

from terminal.services.containers import (
    PAGE_SIZE,
    _carry_into,
    default_carry_target,
    default_work_date,
    unverified_backlog,
)
from .arrivals import arrival_create_view
from .carryover import carryover_decide_view, carryover_view
from .containers import (
    container_delete_view,
    container_detail_view,
    container_edit_view,
    container_list_view,
    container_restore_view,
    container_status_bulk_view,
    container_update_status_view,
)
from .dashboard import dashboard_view
from .inspection import (
    container_set_verification_view,
    container_verify_bulk_view,
    inspection_view,
)
from .prepare import (
    container_add_bulk_view,
    container_add_single_view,
    prepare_view,
    registered_add_to_list_view,
    registered_search_json_view,
)
from .public import (
    client_watchlist_remove_view,
    client_watchlist_state_view,
    client_watchlist_view,
    home_view,
    pwa_manifest_view,
    pwa_service_worker_view,
    verification_state_view,
    verification_view,
)
from .reports import (
    container_export_csv_view,
    container_export_pdf_view,
    register_view,
    statistics_view,
    status_logs_view,
)
from .settings import (
    HeroSlideForm,
    SiteSettingsForm,
    hero_slide_create_view,
    hero_slide_delete_view,
    hero_slide_toggle_view,
    motif_create_view,
    motif_toggle_view,
    motif_update_view,
    site_settings_view,
)
from .shipping_lines import (
    shipping_line_create_view,
    shipping_line_delete_view,
    shipping_line_edit_view,
    shipping_lines_view,
)

__all__ = [
    'PAGE_SIZE',
    'default_carry_target',
    'default_work_date',
    'unverified_backlog',
    'arrival_create_view',
    'carryover_decide_view',
    'carryover_view',
    'container_delete_view',
    'container_detail_view',
    'container_edit_view',
    'container_list_view',
    'container_restore_view',
    'container_status_bulk_view',
    'container_update_status_view',
    'dashboard_view',
    'container_set_verification_view',
    'container_verify_bulk_view',
    'inspection_view',
    'container_add_bulk_view',
    'container_add_single_view',
    'prepare_view',
    'registered_add_to_list_view',
    'registered_search_json_view',
    'client_watchlist_remove_view',
    'client_watchlist_state_view',
    'client_watchlist_view',
    'home_view',
    'pwa_manifest_view',
    'pwa_service_worker_view',
    'verification_state_view',
    'verification_view',
    'container_export_csv_view',
    'container_export_pdf_view',
    'register_view',
    'statistics_view',
    'status_logs_view',
    'HeroSlideForm',
    'SiteSettingsForm',
    'hero_slide_create_view',
    'hero_slide_delete_view',
    'hero_slide_toggle_view',
    'motif_create_view',
    'motif_toggle_view',
    'motif_update_view',
    'site_settings_view',
    'shipping_line_create_view',
    'shipping_line_delete_view',
    'shipping_line_edit_view',
    'shipping_lines_view',
]
