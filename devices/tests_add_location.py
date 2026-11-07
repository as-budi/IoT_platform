"""Form device: tombol "+ Add location" lalu kembali dengan isian yang sama dan lokasi baru."""
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from sharing.models import DeviceShare

from .models import Device, Location

User = get_user_model()


class AddLocationFromDeviceFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")

    def setUp(self):
        self.client.force_login(self.siti)

    def go_add_location(self, url, **fields):
        response = self.client.post(url, {"add_location": "1", **fields})
        self.assertEqual(response.status_code, 302)
        location_url = response["Location"]
        self.assertTrue(location_url.startswith(reverse("devices:location_add") + "?next="))
        return location_url, parse_qs(urlparse(location_url).query)["next"][0]

    def test_add_device_round_trip(self):
        add = reverse("devices:add")
        page = self.client.get(add)
        self.assertContains(page, 'name="add_location"')
        location_url, back = self.go_add_location(
            add, name="Node F5.4", device_type="ESP32+DHT11", description="Room F3.7", board="", location="")
        self.assertFalse(Device.objects.exists())                         # belum disimpan
        form = self.client.get(location_url)
        self.assertContains(form, f'name="next" value="{back}"')
        self.assertContains(form, "Save and go back")
        response = self.client.post(location_url, {"name": "Gedung F", "latitude": "-7.95", "longitude": "112.61",
                                                   "next": back})
        new = Location.objects.get(name="Gedung F")
        self.assertRedirects(response, f"{back}&location={new.pk}", fetch_redirect_response=False)
        page = self.client.get(response["Location"])
        initial = page.context["form"].initial
        self.assertEqual((initial["name"], initial["device_type"], initial["description"]),
                         ("Node F5.4", "ESP32+DHT11", "Room F3.7"))
        self.assertFalse(initial["is_active"])                            # kotak tidak dicentang tadi
        self.assertEqual(initial["location"], new.pk)
        self.assertContains(page, f'<option value="{new.pk}" selected>Gedung F</option>', html=True)

    def test_edit_device_round_trip(self):
        device = Device.objects.create(owner=self.siti, name="Old name")
        edit = reverse("devices:edit", args=[device.pk])
        _, back = self.go_add_location(edit, name="New name", is_active="on")
        page = self.client.get(back)
        self.assertEqual(page.context["form"].initial["name"], "New name")
        device.refresh_from_db()
        self.assertEqual(device.name, "Old name")                         # belum disimpan

    def test_next_must_be_a_device_form_on_this_site(self):
        url = reverse("devices:location_add")
        for bad in ("https://evil.example/devices/add/", "//evil.example/devices/", "/accounts/logout/"):
            response = self.client.post(f"{url}?next={bad}", {"name": f"L {bad}", "latitude": "1", "longitude": "1",
                                                              "next": bad})
            location = Location.objects.get(name=f"L {bad}")
            self.assertRedirects(response, reverse("devices:location_detail", args=[location.pk]),
                                 fetch_redirect_response=False)

    def test_no_button_for_editor_of_someone_elses_device(self):
        device = Device.objects.create(owner=self.budi, name="Budi node")
        DeviceShare.objects.create(device=device, user=self.siti, role=DeviceShare.Role.EDITOR)
        page = self.client.get(reverse("devices:edit", args=[device.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'name="add_location"')
