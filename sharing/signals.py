"""
Log aktivitas berbagi device (tahap 19, kategori "Sharing").

Siapa melihat barisnya (lihat activity/log.visible_events):
- share dibuat / diubah  -> owner = pemilik device, device = device itu, sehingga
  pemilik DAN semua yang berbagi device itu melihatnya;
- share dicabut          -> baris yang sama, ditambah satu baris untuk penerimanya
  (ia tidak lagi melihat device itu, tetapi tetap tahu aksesnya dicabut);
- anggota grup            -> baris untuk pemilik grup dan untuk anggota tersebut.
"""
from django.db.models.signals import post_delete, post_init, post_save
from django.dispatch import receiver

from activity.log import Category, Level, record
from activity.signals import _own_delete

from .models import DeviceShare, ShareGroup, ShareGroupMember


@receiver(post_init, sender=DeviceShare)
def share_loaded(sender, instance, **kwargs):
    instance._activity_role = instance.role


@receiver(post_save, sender=DeviceShare)
def share_saved(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    device = instance.device
    role = instance.get_role_display()
    if created:
        record("share.added", f"{device.name} shared with {instance.target_name} as {role}",
               category=Category.SHARING, owner=device.owner, device=device)
    elif instance._activity_role != instance.role:
        before = DeviceShare.Role(instance._activity_role).label
        record("share.changed", f"Access of {instance.target_name} to {device.name}: {before} → {role}",
               category=Category.SHARING, owner=device.owner, device=device)
    instance._activity_role = instance.role


@receiver(post_delete, sender=DeviceShare)
def share_deleted(sender, instance, origin=None, **kwargs):
    # Device atau pengguna dihapus -> share ikut terhapus; tidak perlu dicatat per baris.
    # Grup dihapus -> dicatat sekali oleh group_deleted.
    if not _own_delete(instance, origin):
        return
    device = instance.device
    record("share.removed", f"{instance.target_name} no longer has access to {device.name}",
           category=Category.SHARING, level=Level.WARNING, owner=device.owner, device=device)
    if instance.user_id:
        record("share.removed", f"Your access to {device.name} (owner {device.owner.username}) was removed",
               category=Category.SHARING, level=Level.WARNING, owner=instance.user)


@receiver(post_delete, sender=ShareGroup)
def group_deleted(sender, instance, origin=None, **kwargs):
    if _own_delete(instance, origin):
        record("group.deleted", f"Group \"{instance.name}\" deleted", category=Category.SHARING,
               level=Level.WARNING, owner=instance.owner)


@receiver(post_save, sender=ShareGroupMember)
def member_added(sender, instance, created, raw=False, **kwargs):
    if not created or raw:
        return
    group = instance.group
    if instance.user_id == group.owner_id:
        record("group.created", f"Group \"{group.name}\" created", category=Category.SHARING, owner=group.owner)
        return
    record("group.member_added", f"{instance.user.username} added to group \"{group.name}\"",
           category=Category.SHARING, owner=group.owner)
    record("group.member_added", f"You were added to group \"{group.name}\" (owner {group.owner.username})",
           category=Category.SHARING, owner=instance.user)


@receiver(post_delete, sender=ShareGroupMember)
def member_removed(sender, instance, origin=None, **kwargs):
    if not _own_delete(instance, origin):
        return
    group = instance.group
    record("group.member_removed", f"{instance.user.username} is no longer in group \"{group.name}\"",
           category=Category.SHARING, owner=group.owner)
    record("group.member_removed", f"You are no longer in group \"{group.name}\"",
           category=Category.SHARING, owner=instance.user)
