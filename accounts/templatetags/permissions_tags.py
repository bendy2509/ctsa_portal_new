from django import template
from accounts.permissions import peut

register = template.Library()


@register.simple_tag(takes_context=True)
def can(context, action: str, objet=None) -> bool:
    request = context.get('request')
    user = getattr(request, 'user', None) if request else context.get('user')
    return peut(user, action, objet=objet)
