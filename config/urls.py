from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from terminal import views as terminal_views

admin.site.site_header = "CTSA Portal — Administration Technique"
admin.site.site_title = "CTSA Portal Admin"
admin.site.index_title = "Cap Terminal S.A. — Back-office"

urlpatterns = [
    path('i18n/', include('django.conf.urls.i18n')),
    path('django-admin/', admin.site.urls),
    path('manifest.webmanifest', terminal_views.pwa_manifest_view, name='pwa_manifest_root'),
    path('sw.js', terminal_views.pwa_service_worker_view, name='pwa_sw_root'),
]

urlpatterns += i18n_patterns(
    path('auth/', include('accounts.urls')),
    path('', include('terminal.urls')),
    prefix_default_language=True,
)

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
