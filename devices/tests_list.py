"""Daftar device: pencarian, tombol Edit/Delete per baris, dan nama/lokasi sebagai chip."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from rules.models import EcaSpec, Rule
from sharing.models import DeviceShare

from .models import Device, Location, Variable

User = get_user_model()


class DeviceListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.lab = Location.objects.create(owner=cls.siti, name="FILKOM UB", latitude=-7.95, longitude=112.61)
        cls.node = Device.objects.create(owner=cls.siti, name="Node F5.3", location=cls.lab)
        Variable.objects.create(device=cls.node, key="humidity")
        cls.fan = Device.objects.create(owner=cls.siti, name="Neighbour Fan", mac_address="02:8B:F9:02:40:6F")
        cls.edited = Device.objects.create(owner=cls.budi, name="Budi Editor")
        cls.viewed = Device.objects.create(owner=cls.budi, name="Budi Viewer")
        DeviceShare.objects.create(device=cls.edited, user=cls.siti, role=DeviceShare.Role.EDITOR)
        DeviceShare.objects.create(device=cls.viewed, user=cls.siti, role=DeviceShare.Role.VIEWER)

    def setUp(self):
        self.client.force_login(self.siti)

    def names(self, **params):
        response = self.client.get(reverse("devices:list"), params)
        return {d.name for d in response.context["devices"]}, response

    def test_search_by_name_location_variable_and_mac(self):
        self.assertEqual(self.names(q="node")[0], {"Node F5.3"})
        self.assertEqual(self.names(q="filkom")[0], {"Node F5.3"})          # lokasi
        self.assertEqual(self.names(q="humid")[0], {"Node F5.3"})           # key variabel
        self.assertEqual(self.names(q="40:6f")[0], {"Neighbour Fan"})       # MAC
        names, response = self.names(q="budi")
        self.assertEqual(names, {"Budi Editor", "Budi Viewer"})
        self.assertContains(response, "2 of 4 devices match")
        names, response = self.names(q="nothing-like-this")
        self.assertEqual(names, set())
        self.assertContains(response, "No devices match your search.")

    def test_search_combines_with_filters(self):
        self.assertEqual(self.names(q="fan", access="mine")[0], {"Neighbour Fan"})
        self.assertEqual(self.names(q="fan", access="shared")[0], set())

    def test_actions_follow_permissions(self):
        _, response = self.names()
        html = response.content.decode()
        for device in (self.node, self.fan):                             # milik sendiri
            self.assertIn(reverse("devices:edit", args=[device.pk]), html)
            self.assertIn(reverse("devices:delete", args=[device.pk]), html)
        self.assertIn(reverse("devices:edit", args=[self.edited.pk]), html)   # Editor: ubah saja
        self.assertNotIn(reverse("devices:delete", args=[self.edited.pk]), html)
        self.assertNotIn(reverse("devices:edit", args=[self.viewed.pk]), html)  # Viewer: tidak ada
        self.assertNotIn(reverse("devices:delete", args=[self.viewed.pk]), html)

    def test_names_and_location_are_chips(self):
        _, response = self.names()
        self.assertContains(response, 'class="entity-chip entity-device"', count=4)
        self.assertContains(response, 'class="entity-pill entity-location"', count=1)
        location = self.client.get(reverse("devices:location_detail", args=[self.lab.pk]))
        self.assertContains(location, reverse("devices:edit", args=[self.node.pk]))


class RuleListActionTests(TestCase):
    def test_rule_rows_have_chip_edit_and_delete(self):
        siti = User.objects.create_user("siti", password="rahasia-12345")
        rule = Rule.objects.create(owner=siti, name="Fan automation", is_enabled=False)
        EcaSpec.objects.create(rule=rule)
        self.client.force_login(siti)
        response = self.client.get(reverse("rules:list"))
        self.assertContains(response, 'class="entity-chip entity-rule is-muted"')   # nonaktif: redup
        self.assertContains(response, reverse("rules:edit", args=[rule.pk]))
        self.assertContains(response, reverse("rules:delete", args=[rule.pk]))
        self.assertContains(response, 'aria-label="Delete Fan automation"')
