from django.utils.http import url_has_allowed_host_and_scheme


def safe_next_url(request, candidate: str, fallback: str) -> str:
    """Renvoie `candidate` s'il pointe bien vers ce portail, sinon `fallback`.

    Évite qu'un lien piégé (`?next=https://site-externe`) ne redirige l'utilisateur hors du
    portail après une connexion ou une action.
    """
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return fallback
