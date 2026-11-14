from .views import default_carry_target, unverified_backlog


def portal_navigation(request):
    """Compteur « Non vérifiés » de la sidebar d'administration (personnel uniquement).

    Même périmètre que la page « Non vérifiés » ouverte sans date : les conteneurs à proposer
    pour la liste en préparation.
    """
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated and user.is_staff_member):
        return {}
    return {
        'portal_unverified_count': unverified_backlog(default_carry_target(user)).count(),
    }
