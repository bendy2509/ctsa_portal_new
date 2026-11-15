import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout


class InactivityTimeoutMiddleware:
    """Déconnecte l'utilisateur après une période d'inactivité (5 minutes par défaut).

    Les requêtes automatiques de rafraîchissement en arrière-plan ne prolongent pas la session.
    """

    BACKGROUND_PATHS = (
        '/verification/etat/',
        '/mes-conteneurs/etat/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            if request.path not in self.BACKGROUND_PATHS:
                now = time.time()
                timeout = getattr(settings, 'SESSION_COOKIE_AGE', 300)
                last_activity = request.session.get('_last_activity')
                if last_activity is not None and (now - last_activity) > timeout:
                    logout(request)
                    messages.info(
                        request,
                        "Votre session a expiré après 5 minutes d'inactivité. Veuillez vous reconnecter.",
                    )
                else:
                    request.session['_last_activity'] = now

        return self.get_response(request)
