"""{% load camera_tags %}  ·  {{ snapshot|thumb_url }} = URL thumbnail (atau gambar asli bila belum ada)."""
from django import template

from cameras.services import thumb_url as _thumb_url

register = template.Library()


@register.filter
def thumb_url(snapshot):
    return _thumb_url(snapshot)
