"""Konfigurasi WSGI — pintu masuk server web sinkron (mis. gunicorn) ke project ini."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()
