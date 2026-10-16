# Tahap 12, langkah 2/3: memberi pemilik pada lokasi yang sudah ada.
#
# Sebelumnya satu lokasi bisa dipakai device milik beberapa pengguna. Aturannya:
#   - Pemilik = pengguna yang paling banyak punya device di lokasi itu
#     (bila seri: id pengguna terkecil).
#   - Pengguna lain yang juga punya device di sana mendapat SALINAN lokasi
#     (nama, koordinat, deskripsi sama), dan device-nya dipindah ke salinan itu.
#     Pengaturan dashboard "sembunyikan lokasi ini" ikut dipindah ke salinan.
#   - Lokasi tanpa device diberikan ke Admin pertama (atau pengguna pertama).
#     Bila belum ada pengguna sama sekali, lokasi itu dihapus.
from django.db import migrations
from django.db.models import Count, Q


def assign_owners(apps, schema_editor):
    Location = apps.get_model("devices", "Location")
    Device = apps.get_model("devices", "Device")
    User = apps.get_model("accounts", "User")
    Preference = apps.get_model("dashboard", "DashboardPreference")

    fallback = (
        User.objects.filter(Q(is_superuser=True) | Q(role="ADMIN")).order_by("pk").first()
        or User.objects.order_by("pk").first()
    )

    for location in Location.objects.filter(owner__isnull=True).order_by("pk"):
        # [{"owner_id": 3, "n": 2}, {"owner_id": 5, "n": 1}, ...] urut terbanyak.
        owners = list(
            Device.objects.filter(location=location)
            .values("owner_id")
            .annotate(n=Count("pk"))
            .order_by("-n", "owner_id")
        )
        if not owners:
            if fallback is None:
                location.delete()
            else:
                location.owner_id = fallback.pk
                location.save(update_fields=["owner"])
            continue

        location.owner_id = owners[0]["owner_id"]
        location.save(update_fields=["owner"])
        for row in owners[1:]:
            copy = Location.objects.create(
                owner_id=row["owner_id"],
                name=location.name,
                description=location.description,
                latitude=location.latitude,
                longitude=location.longitude,
            )
            Device.objects.filter(location=location, owner_id=row["owner_id"]).update(location=copy)
            for preference in Preference.objects.filter(user_id=row["owner_id"], hidden_locations=location):
                preference.hidden_locations.remove(location)
                preference.hidden_locations.add(copy)


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0017_location_owner"),
        ("accounts", "0002_email_verification"),
        ("dashboard", "0001_initial"),
    ]

    # Arah sebaliknya tidak mengembalikan apa pun (salinan lokasi tetap ada).
    operations = [migrations.RunPython(assign_owners, migrations.RunPython.noop)]
