from django import template
from django.urls import translate_url

register = template.Library()


@register.simple_tag(takes_context=True)
def switch_lang_url(context, lang_code: str) -> str:
    request = context.get('request')
    if not request:
        return f'/{lang_code}/'
    return translate_url(request.get_full_path(), lang_code)
