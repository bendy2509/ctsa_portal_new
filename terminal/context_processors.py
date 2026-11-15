from .models import SiteSettings
from .views import default_carry_target, unverified_backlog


def portal_navigation(request):
    """Injecte les paramètres publics du site et le compteur « Non vérifiés »."""
    context = {
        'site_settings': SiteSettings.get_solo(),
    }
    user = getattr(request, 'user', None)
    if user and user.is_authenticated and user.is_staff_member:
        context['portal_unverified_count'] = unverified_backlog(default_carry_target(user)).count()
    return context
