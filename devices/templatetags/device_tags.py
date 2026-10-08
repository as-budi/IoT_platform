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
