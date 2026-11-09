"""
python manage.py camera_thumbnails

Membuat thumbnail untuk snapshot yang belum punya, mis. gambar yang tiba sebelum Pillow
dipasang (tahap 27a). Gambar baru selalu mendapat thumbnail saat diunggah.
"""
from django.core.management.base import BaseCommand, CommandError

from cameras import services, thumbs


class Command(BaseCommand):
    help = "Create missing thumbnails for camera snapshots (needs Pillow)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="At most this many images.")

    def handle(self, *args, limit=None, **options):
        if not thumbs.available():
            raise CommandError("Pillow is not installed: pip install Pillow")
        made = services.make_missing_thumbnails(limit=limit)
        self.stdout.write(self.style.SUCCESS(f"{made} thumbnail(s) created."))
