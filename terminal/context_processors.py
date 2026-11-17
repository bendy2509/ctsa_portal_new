from django.utils.translation import get_language

from .models import Motif, SiteSettings
from .views import default_carry_target, unverified_backlog


def portal_navigation(request):
    """Injecte les paramètres publics du site et le compteur « Non vérifiés »."""
    site_obj = SiteSettings.get_solo()
    context = {
        'site_settings': site_obj,
        'site_t': site_obj.for_language(get_language()),
    }
    user = getattr(request, 'user', None)
    if user and user.is_authenticated and user.is_staff_member:
        context['portal_unverified_count'] = unverified_backlog(default_carry_target(user)).count()
        context['unavailable_motifs'] = list(
            Motif.objects.filter(type=Motif.Type.UNAVAILABLE, is_active=True)
        )
        context['not_verified_motifs'] = list(
            Motif.objects.filter(type=Motif.Type.NOT_VERIFIED, is_active=True)
        )
    return context
