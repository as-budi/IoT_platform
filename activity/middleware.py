"""Simpan request saat ini supaya log aktivitas tahu siapa pelakunya (activity/log.py).

Yang disimpan adalah request, bukan request.user (objek lazy); alasannya ada di
komentar `current_request` di activity/log.py.
"""
from .log import current_request


class ActorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = current_request.set(request)
        try:
            return self.get_response(request)
        finally:
            current_request.reset(token)
