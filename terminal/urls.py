from django.urls import path
from . import views

app_name = 'terminal'

urlpatterns = [
    # PWA
    path('manifest.webmanifest', views.pwa_manifest_view, name='pwa_manifest'),
    path('sw.js', views.pwa_service_worker_view, name='pwa_sw'),

    # Partie publique
    path('', views.home_view, name='home'),
    path('verification/', views.verification_view, name='verification'),
    path('verification/etat/', views.verification_state_view, name='verification_state'),
    path('mes-conteneurs/', views.client_watchlist_view, name='client_watchlist'),
    path('mes-conteneurs/etat/', views.client_watchlist_state_view, name='client_watchlist_state'),
    path('mes-conteneurs/<int:pk>/retirer/', views.client_watchlist_remove_view, name='client_watchlist_remove'),

    # Partie interne (/admin)
    path('admin/', views.dashboard_view, name='dashboard'),
    path('admin/conteneurs/', views.container_list_view, name='containers'),
    path('admin/conteneurs/<int:pk>/', views.container_detail_view, name='container_detail'),
    path('admin/conteneurs/<int:pk>/modifier/', views.container_edit_view, name='container_edit'),
    path('admin/arrivees/nouveau/', views.arrival_create_view, name='arrival_create'),
    path('admin/preparer/', views.prepare_view, name='prepare'),
    path('admin/preparer/rechercher-enregistres/', views.registered_search_json_view, name='registered_search_json'),
    path('admin/preparer/ajouter-enregistres/', views.registered_add_to_list_view, name='registered_add_to_list'),
    path('admin/containers/add/', views.container_add_single_view, name='container_add_single'),
    path('admin/containers/bulk-add/', views.container_add_bulk_view, name='container_add_bulk'),
    path('admin/containers/<int:pk>/status/', views.container_update_status_view, name='container_update_status'),
    path('admin/containers/status-lot/', views.container_status_bulk_view, name='container_status_bulk'),
    path('admin/containers/<int:pk>/delete/', views.container_delete_view, name='container_delete'),
    path('admin/conteneurs/<int:pk>/restaurer/', views.container_restore_view, name='container_restore'),
    path('admin/containers/<int:pk>/verification/', views.container_set_verification_view, name='container_set_verification'),
    path('admin/containers/verification-lot/', views.container_verify_bulk_view, name='container_verify_bulk'),
    path('admin/inspection/', views.inspection_view, name='inspection'),
    path('admin/registre/', views.register_view, name='register'),
    path('admin/non-verifies/', views.carryover_view, name='carryover'),
    path('admin/non-verifies/decision/', views.carryover_decide_view, name='carryover_decide'),
    path('admin/statistiques/', views.statistics_view, name='statistics'),
    path('admin/containers/export-pdf/', views.container_export_pdf_view, name='container_export_pdf'),
    path('admin/containers/export-csv/', views.container_export_csv_view, name='container_export_csv'),
    path('admin/historique/', views.status_logs_view, name='status_logs'),
    path('admin/lignes-maritimes/', views.shipping_lines_view, name='shipping_lines'),
    path('admin/lignes-maritimes/nouveau/', views.shipping_line_create_view, name='shipping_line_create'),
    path('admin/lignes-maritimes/<int:pk>/modifier/', views.shipping_line_edit_view, name='shipping_line_edit'),
    path('admin/lignes-maritimes/<int:pk>/supprimer/', views.shipping_line_delete_view, name='shipping_line_delete'),
    path('admin/parametres-site/', views.site_settings_view, name='site_settings'),
    path('admin/parametres-site/textes/', views.site_settings_texts_view, name='site_settings_texts'),
    path('admin/parametres-site/fenetres/', views.site_settings_windows_view, name='site_settings_windows'),
    path('admin/parametres-site/carrousel/', views.site_settings_carousel_view, name='site_settings_carousel'),
    path('admin/parametres-site/motifs/', views.site_settings_motifs_view, name='site_settings_motifs'),
    path('admin/parametres-site/carrousel/ajouter/', views.hero_slide_create_view, name='hero_slide_create'),
    path('admin/parametres-site/carrousel/<int:pk>/basculer/', views.hero_slide_toggle_view, name='hero_slide_toggle'),
    path('admin/parametres-site/carrousel/<int:pk>/supprimer/', views.hero_slide_delete_view, name='hero_slide_delete'),
    path('admin/parametres-site/motifs/ajouter/', views.motif_create_view, name='motif_create'),
    path('admin/parametres-site/motifs/<int:pk>/basculer/', views.motif_toggle_view, name='motif_toggle'),
    path('admin/parametres-site/motifs/<int:pk>/modifier/', views.motif_update_view, name='motif_update'),
]
