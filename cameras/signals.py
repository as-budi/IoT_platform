"""File gambar ikut terhapus bila baris Snapshot dihapus (manual, retensi, atau device dihapus)."""
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Snapshot


@receiver(post_delete, sender=Snapshot)
def delete_file(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)
