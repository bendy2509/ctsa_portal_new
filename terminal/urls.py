from django.urls import path
from . import views

app_name = 'terminal'

urlpatterns = [
    # Partie publique
    path('', views.home_view, name='home'),
    path('verification/', views.verification_view, name='verification'),
    path('mes-conteneurs/', views.client_watchlist_view, name='client_watchlist'),
    path('mes-conteneurs/<int:pk>/retirer/', views.client_watchlist_remove_view, name='client_watchlist_remove'),

    # Partie interne (/admin)
    path('admin/', views.dashboard_view, name='dashboard'),
    path('admin/containers/add/', views.container_add_single_view, name='container_add_single'),
    path('admin/containers/bulk-add/', views.container_add_bulk_view, name='container_add_bulk'),
    path('admin/containers/<int:pk>/status/', views.container_update_status_view, name='container_update_status'),
    path('admin/containers/<int:pk>/delete/', views.container_delete_view, name='container_delete'),
    path('admin/containers/<int:pk>/verification/', views.container_set_verification_view, name='container_set_verification'),
    path('admin/containers/verification-lot/', views.container_verify_bulk_view, name='container_verify_bulk'),
    path('admin/non-verifies/', views.carryover_view, name='carryover'),
    path('admin/non-verifies/decision/', views.carryover_decide_view, name='carryover_decide'),
    path('admin/statistiques/', views.statistics_view, name='statistics'),
    path('admin/containers/export-pdf/', views.container_export_pdf_view, name='container_export_pdf'),
    path('admin/containers/export-csv/', views.container_export_csv_view, name='container_export_csv'),
    path('admin/historique/', views.status_logs_view, name='status_logs'),
    path('admin/motifs/', views.prefixes_view, name='prefixes'),
    path('admin/prefixes/', views.prefixes_view, name='prefixes_legacy'),
    path('admin/lignes-maritimes/', views.shipping_lines_view, name='shipping_lines'),
]
