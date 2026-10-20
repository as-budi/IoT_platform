"""
Filter template buatan sendiri. Dipakai di template dengan:

    {% load device_tags %}
    {{ row.value|show_value:variable }}

File di <app>/templatetags/ otomatis dikenali Django (nama modul = nama
yang dipakai di {% load %}).
"""
import json

from django import template
from django.template.defaultfilters import floatformat

register = template.Library()


@register.filter
def show_value(value, variable):
    """Tampilkan nilai sesuai tipe variabel: ON/OFF untuk boolean, 2 desimal untuk angka."""
    if value is None:
        return "—"
    if variable.is_boolean:
        return "ON" if value >= 0.5 else "OFF"
    return floatformat(value, 2)


@register.filter
def as_json(value):
    """dict/list -> teks JSON ringkas, untuk menampilkan params & response command."""
    if value in (None, {}, []):
        return "—"
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


@register.filter
def plain_number(value):
    """Angka tanpa format lokal (titik desimal, tanpa pemisah ribuan).

    Dipakai di atribut HTML seperti <input value="..." min="...">, yang hanya
    menerima format angka standar, apa pun bahasa tampilannya.
    """
    if value is None:
        return ""
    return format(value, "g")


@register.simple_tag(takes_context=True)
def can_access(context, device_id, level):
    """{% can_access variable.device_id "OPERATOR" as can_operate %} (tahap 19).

    Peran disimpan di cache per request (sharing.access.request_cache), jadi
    dashboard dengan puluhan aktuator tidak membuat puluhan query.
    """
    from sharing import access

    request = context.get("request")
    if request is None:
        return False
    return access.can(request.user, device_id, level, cache=access.request_cache(request))
