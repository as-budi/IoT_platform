"""Tes tahap 27a: snapshot ESP32-CAM."""
import shutil
import struct
import tempfile
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from devices.models import Board, Command, Device
from devices.views import data_version
from sharing.models import DeviceShare, Role

from . import jpeg, services
from .models import Camera, Snapshot

User = get_user_model()


def make_jpeg(width=640, height=480, padding=100):
    """JPEG kecil yang sah strukturnya: SOI, APP0, SOF0 (ukuran), data, EOI."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3) + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xda" + b"\x00" * padding + b"\xff\xd9"


class CameraData(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage_dir = tempfile.mkdtemp(prefix="camera-tests-")
        cls.storage = override_settings(CAMERA_STORAGE_DIR=cls.storage_dir, CAMERA_MIN_INTERVAL_S=2)
        cls.storage.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.storage.disable()
        shutil.rmtree(cls.storage_dir, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        cls.siti = User.objects.create_user("siti", password="rahasia-12345")
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.rina = User.objects.create_user("rina", password="rahasia-12345")
        cls.device = Device.objects.create(owner=cls.siti, name="Gate camera", board=Board.ESP32CAM)
        DeviceShare.objects.create(device=cls.device, user=cls.rina, role=Role.VIEWER)

    def upload(self, data=None, token=None, **headers):
        return self.client.post(
            reverse("api:snapshot", args=[token or self.device.access_token]),
            data=make_jpeg() if data is None else data, content_type="image/jpeg",
            **{f"HTTP_{k.upper().replace('-', '_')}": v for k, v in headers.items()},
        )


class JpegTests(TestCase):
    def test_dimensions(self):
        self.assertTrue(jpeg.is_jpeg(make_jpeg()))
        self.assertEqual(jpeg.dimensions(make_jpeg(800, 600)), (800, 600))
        self.assertFalse(jpeg.is_jpeg(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(jpeg.dimensions(b"\xff\xd8\xff\xd9"), (None, None))


class UploadTests(CameraData):
    def test_upload_saves_the_image_and_marks_the_device_online(self):
        response = self.upload(x_source="periodic")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["width"], 640)
        snapshot = Snapshot.objects.get()
        self.assertEqual((snapshot.camera.name, snapshot.width, snapshot.height), ("camera", 640, 480))
        self.assertEqual(snapshot.source, Snapshot.Source.PERIODIC)
        self.assertTrue(Path(snapshot.file.path).exists())
        self.assertTrue(snapshot.file.path.startswith(self.storage_dir))
        self.device.refresh_from_db()
        self.assertTrue(self.device.is_online)
        self.assertIsNotNone(self.device.last_seen_at)

    def test_rejections(self):
        self.assertEqual(self.upload(token="wrong").status_code, 401)
        self.assertEqual(self.upload(data=b"").status_code, 400)
        self.assertEqual(self.upload(data=b"\x89PNG\r\n\x1a\nxxxx").status_code, 415)
        with override_settings(CAMERA_MAX_SNAPSHOT_KB=1):
            self.assertEqual(self.upload(data=make_jpeg(padding=2000)).status_code, 413)
        self.assertEqual(self.client.get(reverse("api:snapshot", args=[self.device.access_token])).status_code, 405)
        Device.objects.filter(pk=self.device.pk).update(is_active=False)
        self.assertEqual(self.upload().status_code, 403)
        self.assertFalse(Snapshot.objects.exists())

    def test_too_often_except_for_a_capture_command(self):
        self.assertEqual(self.upload().status_code, 201)
        self.assertEqual(self.upload().status_code, 429)
        command = services.request_capture(self.device, self.siti)
        response = self.upload(x_command_id=str(command.pk))
        self.assertEqual(response.status_code, 201)
        snapshot = Snapshot.objects.get(pk=response.json()["id"])
        self.assertEqual((snapshot.source, snapshot.command), (Snapshot.Source.COMMAND, command))

    def test_new_snapshot_changes_the_page_version(self):
        before = data_version(self.device)
        self.upload()
        self.assertNotEqual(data_version(self.device), before)


class PageTests(CameraData):
    def setUp(self):
        self.client.force_login(self.siti)
        self.assertEqual(self.upload().status_code, 201)
        self.snapshot = Snapshot.objects.get()

    def test_device_page_card_and_image(self):
        page = self.client.get(reverse("devices:detail", args=[self.device.pk]))
        self.assertContains(page, 'id="camera"')
        self.assertContains(page, reverse("cameras:image", args=[self.snapshot.pk]))
        self.assertContains(page, "Capture now")
        image = self.client.get(reverse("cameras:image", args=[self.snapshot.pk]))
        self.assertEqual(image["Content-Type"], "image/jpeg")
        self.assertEqual(b"".join(image.streaming_content), make_jpeg())
        download = self.client.get(reverse("cameras:image", args=[self.snapshot.pk]), {"download": 1})
        self.assertIn("attachment", download["Content-Disposition"])

    @override_settings(CAMERA_X_ACCEL_PREFIX="/protected/camera/")
    def test_nginx_sends_the_file_in_production(self):
        image = self.client.get(reverse("cameras:image", args=[self.snapshot.pk]))
        self.assertEqual(image["X-Accel-Redirect"], "/protected/camera/" + self.snapshot.file.name)

    def test_access_follows_sharing(self):
        url = reverse("cameras:image", args=[self.snapshot.pk])
        self.client.force_login(self.budi)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(reverse("cameras:gallery", args=[self.device.pk])).status_code, 404)
        self.client.force_login(self.rina)                      # Viewer
        self.assertEqual(self.client.get(url).status_code, 200)
        gallery = self.client.get(reverse("cameras:gallery", args=[self.device.pk]))
        self.assertContains(gallery, url)
        self.assertNotContains(gallery, "Capture now")
        self.assertEqual(self.client.post(reverse("cameras:capture", args=[self.device.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("cameras:delete", args=[self.snapshot.pk])).status_code, 403)

    def test_capture_sends_a_command(self):
        response = self.client.post(reverse("cameras:capture", args=[self.device.pk]))
        self.assertRedirects(response, reverse("devices:detail", args=[self.device.pk]) + "#camera",
                             fetch_redirect_response=False)
        command = Command.objects.get(device=self.device)
        self.assertEqual((command.method, command.params), ("capture", {}))

    def test_gallery_day_filter_and_delete(self):
        gallery = reverse("cameras:gallery", args=[self.device.pk])
        today = timezone.localdate().isoformat()
        self.assertContains(self.client.get(gallery, {"day": today}), "1 image")
        self.assertContains(self.client.get(gallery, {"day": "2000-01-01"}), "No snapshots on this day")
        self.assertContains(self.client.get(gallery, {"day": "nonsense"}), "No snapshots")
        path = Path(self.snapshot.file.path)
        self.client.post(reverse("cameras:delete", args=[self.snapshot.pk]))
        self.assertFalse(Snapshot.objects.exists())
        self.assertFalse(path.exists(), "the file is removed with the row")

    def test_retention(self):
        Snapshot.objects.filter(pk=self.snapshot.pk).update(ts=timezone.now() - timedelta(days=40))
        self.assertEqual(services.delete_expired(), 1)               # bawaan 30 hari
        self.client.post(reverse("cameras:settings", args=[self.device.pk]), {"retention_days": "0"})
        self.assertEqual(Camera.objects.get().retention_days, 0)
        services.save_snapshot(self.device, make_jpeg(), now=timezone.now() - timedelta(days=400))
        self.assertEqual(services.delete_expired(), 0)                # 0 = simpan selamanya
        self.client.post(reverse("cameras:settings", args=[self.device.pk]), {"retention_days": ""})
        self.assertIsNone(Camera.objects.get().retention_days)

    def test_card_hidden_for_other_boards_without_snapshots(self):
        other = Device.objects.create(owner=self.siti, name="Weather", board=Board.ESP32)
        self.assertNotContains(self.client.get(reverse("devices:detail", args=[other.pk])), 'id="camera"')


class TemplateTests(CameraData):
    @override_settings(OTA_BASE_URL="https://iot.example.org")
    def test_esp32cam_template_download(self):
        import io
        import zipfile

        self.client.force_login(self.siti)
        response = self.client.get(reverse("devices:connect_firmware", args=["mqtt"]), {"device": self.device.pk})
        self.assertIn('filename="esp32cam-mqtt-gate-camera.zip"', response["Content-Disposition"])
        files = zipfile.ZipFile(io.BytesIO(response.content))
        source = files.read("esp32cam-mqtt-gate-camera/esp32cam_mqtt/esp32cam_mqtt.ino").decode()
        self.assertIn('const char* SERVER       = "https://iot.example.org";', source)
        self.assertIn(f'"{self.device.access_token}"', source)
        self.assertIn('#define FW_BOARD   "esp32cam"', source)
        self.assertNotIn("paste ISRG Root X1 here", source)
