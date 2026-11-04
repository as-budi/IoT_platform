"""
python manage.py export_microrule_vectors

Membuat ulang test vector micro rule di firmware/microrule/vectors/ dari evaluator
referensi (rules/edge/vectors.py). Jalankan setelah mengubah semantik evaluator atau
format frame, lalu commit file yang berubah.
"""
from django.core.management.base import BaseCommand

from rules.edge import vectors


class Command(BaseCommand):
    help = "Regenerate micro rule test vectors in firmware/microrule/vectors/."

    def add_arguments(self, parser):
        parser.add_argument("--out", default=str(vectors.VECTOR_DIR), help="Target directory.")

    def handle(self, *args, **options):
        written = vectors.write(options["out"])
        self.stdout.write(self.style.SUCCESS(f"{len(written)} vector files written to {options['out']}"))
