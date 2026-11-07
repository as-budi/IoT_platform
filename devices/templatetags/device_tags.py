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
    """Tampilkan nilai sesuai tipe variabel: ON/OFF, label kategori, atau angka 2 desimal."""
    if value is None:
        return "—"
    if variable.is_discrete:
        return variable.format_value(value)
    return floatformat(value, 2)


@register.filter
def show_threshold(value, variable):
    """Ambang kondisi / nilai aksi: label untuk ON/OFF dan kategori bila tepat, selain itu angka."""
    if value is None:
        return "—"
    if variable is not None and variable.is_discrete:
        labels = variable.value_labels
        if value == int(value) and 0 <= value < len(labels):
            return labels[int(value)]
    return floatformat(value, -2)


@register.filter
def category_label(value, categories):
    """Kode -> label dari daftar `categories` (untuk data yang sudah berupa dict)."""
    from devices.models import category_label as label_of

    label = label_of(value, categories)
    return label if label is not None else ("—" if value is None else f"#{value:g}")


@register.filter
def json_attr(value):
    """Nilai Python -> teks JSON untuk atribut data-* (di-escape otomatis oleh template)."""
    return json.dumps(value, ensure_ascii=False)


@register.filter
def as_json(value):
    """dict/list -> teks JSON ringkas, untuk menampilkan params & response command."""
    if value in (None, {}, []):
        return "—"
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


@register.filter
def duration_ms(value):
    """182.4 -> "182 ms", 2350 -> "2.35 s", 125000 -> "2 min 5 s" (round trip time command)."""
    if value is None:
        return ""
    if value < 1000:
        return f"{value:.0f} ms"
    seconds = value / 1000
    if seconds < 60:
        return f"{seconds:.2f} s"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes} min {rest} s"


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
