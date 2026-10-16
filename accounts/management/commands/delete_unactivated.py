"""
Perintah kustom:  python manage.py delete_unactivated [--days 7] [--dry-run]

Menghapus akun hasil Sign up yang tautan aktivasinya TIDAK PERNAH diklik dan
sudah lebih tua dari --days hari. Tanpa pembersihan, siapa pun bisa "mengunci"
username atau email orang lain dengan mendaftar memakai alamat itu lalu tidak
pernah mengaktifkannya.

Yang dihapus hanya akun dengan activation_pending=True dan is_active=False.
Akun yang dinonaktifkan Admin (activation_pending=False) tidak tersentuh.
Di server, perintah ini dijalankan setiap hari oleh iot-cleanup.timer.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User


class Command(BaseCommand):
    help = "Delete sign-ups whose activation link was never opened."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7, help="Minimum age in days (default 7).")
        parser.add_argument("--dry-run", action="store_true", help="Only list the accounts.")

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options["days"])
        stale = User.objects.filter(activation_pending=True, is_active=False, date_joined__lt=cutoff)
        names = list(stale.values_list("username", flat=True))
        if options["dry_run"]:
            self.stdout.write(f"Would delete {len(names)} account(s): {', '.join(names) or '-'}")
            return
        stale.delete()
        self.stdout.write(f"Deleted {len(names)} unactivated account(s).")
