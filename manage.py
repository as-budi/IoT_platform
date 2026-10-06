#!/usr/bin/env python
"""Utilitas baris-perintah Django untuk tugas administratif."""
import os
import sys


def main():
    """Jalankan tugas administratif."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django tidak bisa di-import. Pastikan sudah ter-install dan "
            "virtual environment sudah diaktifkan."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
