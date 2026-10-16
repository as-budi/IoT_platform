"""
Tes migrasi data tahap 12 (devices 0018): pemilik untuk lokasi yang sudah ada.

TransactionTestCase + MigrationExecutor: database dimundurkan ke keadaan
sebelum migrasi, diisi data "lama", lalu dimajukan lagi, persis seperti yang
terjadi di server saat `migrate` dijalankan.
"""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [("devices", "0017_location_owner"), ("accounts", "0002_email_verification"), ("dashboard", "0001_initial")]
AFTER = [("devices", "0019_location_owner_required")]


class LocationOwnerMigrationTests(TransactionTestCase):
    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_shared_locations_are_split_per_owner(self):
        old = self.migrate(BEFORE)
        User = old.get_model("accounts", "User")
        Location = old.get_model("devices", "Location")
        Device = old.get_model("devices", "Device")
        Preference = old.get_model("dashboard", "DashboardPreference")

        admin = User.objects.create(username="admin", role="ADMIN")
        budi = User.objects.create(username="budi")
        siti = User.objects.create(username="siti")
        lab = Location.objects.create(name="Lab", latitude=1, longitude=2, description="Shared lab")
        empty = Location.objects.create(name="Empty", latitude=0, longitude=0)
        # Lab dipakai 2 device siti dan 1 device budi -> siti pemiliknya, budi dapat salinan.
        Device.objects.create(owner=siti, name="S1", location=lab, access_token="t1")
        Device.objects.create(owner=siti, name="S2", location=lab, access_token="t2")
        budi_node = Device.objects.create(owner=budi, name="B1", location=lab, access_token="t3")
        budi_pref = Preference.objects.create(user=budi)
        budi_pref.hidden_locations.add(lab)

        new = self.migrate(AFTER)
        Location = new.get_model("devices", "Location")
        Device = new.get_model("devices", "Device")
        Preference = new.get_model("dashboard", "DashboardPreference")

        labs = {loc.owner_id: loc for loc in Location.objects.filter(name="Lab")}
        self.assertEqual(set(labs), {siti.pk, budi.pk})
        self.assertEqual(labs[siti.pk].pk, lab.pk)                  # lokasi asli tetap, milik siti
        self.assertEqual(labs[budi.pk].description, "Shared lab")   # salinan untuk budi
        self.assertEqual(Device.objects.get(pk=budi_node.pk).location_id, labs[budi.pk].pk)
        self.assertEqual(
            list(Preference.objects.get(user_id=budi.pk).hidden_locations.values_list("pk", flat=True)),
            [labs[budi.pk].pk],
        )
        self.assertEqual(Location.objects.get(pk=empty.pk).owner_id, admin.pk)
