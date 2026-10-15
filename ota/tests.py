"""Tes OTA (tahap 9): paket firmware, mulai/batal, unduhan bertoken, laporan device, batas waktu, halaman."""
import hashlib
import json
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from coap_gateway import handlers as coap
from devices import control
from devices.models import Board, CommandStatus, Device
from mqtt_bridge.handlers import handle_message

from . import services
from .forms import FirmwareUploadForm
from .models import FirmwarePackage, OtaStatus, OtaUpdate

User = get_user_model()
BASE = "http://192.168.1.10:8000"
IMAGE = b"\xe9" + bytes(range(256)) * 40          # "image ESP": byte pertama 0xE9
IMAGE_MD5 = hashlib.md5(IMAGE).hexdigest()


class OtaData(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage_dir = tempfile.mkdtemp(prefix="ota-tests-")
        cls.storage = override_settings(OTA_STORAGE_DIR=cls.storage_dir)
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
        cls.device = Device.objects.create(owner=cls.siti, name="Greenhouse Node", board=Board.ESP32)
        Device.objects.filter(pk=cls.device.pk).update(firmware_version="1.0.0")
        cls.device.refresh_from_db()
        cls.package = cls.make_package(cls.siti, "1.1.0")

    @classmethod
    def make_package(cls, user, version, board=Board.ESP32, content=IMAGE):
        form = FirmwareUploadForm(
            {"title": "Greenhouse firmware", "version": version, "board": board, "notes": ""},
            {"file": SimpleUploadedFile("greenhouse.ino.bin", content)},
            user=user,
        )
        assert form.is_valid(), form.errors
        return form.save()

    def start(self, package=None, device=None):
        return services.start_update(device or self.device, package or self.package, self.siti, BASE)

    def report(self, data, device=None, protocol="MQTT"):
        return services.handle_report(device or self.device, data, protocol)

    def statuses(self, update):
        return [event.status for event in update.events.all()]


class PackageFormTests(OtaData):
    def test_checksums_and_storage(self):
        package = self.package
        self.assertEqual((package.size, package.md5), (len(IMAGE), IMAGE_MD5))
        self.assertEqual(package.sha256, hashlib.sha256(IMAGE).hexdigest())
        with package.file.open("rb") as handle:
            self.assertEqual(handle.read(), IMAGE)
        self.assertTrue(package.file.path.startswith(self.storage_dir), "not in a public media folder")

    def form(self, name="fw.bin", content=IMAGE, board=Board.ESP32, version="2.0.0", user=None):
        return FirmwareUploadForm(
            {"title": "x", "version": version, "board": board},
            {"file": SimpleUploadedFile(name, content)}, user=user or self.siti,
        )

    def test_rejections(self):
        self.assertIn("compiled .bin", str(self.form(name="fw.ino").errors))
        self.assertIn("not an ESP firmware image", str(self.form(content=b"hello").errors))
        self.assertIn("already uploaded this version", str(self.form(version="1.1.0").errors))
        self.assertIn("Use letters, digits", str(self.form(version="1.0 beta").errors))
        with override_settings(OTA_MAX_FIRMWARE_SIZE=100):
            self.assertIn("larger than", str(self.form().errors))

    def test_simulated_board_accepts_any_file_and_versions_are_per_user(self):
        self.assertTrue(self.form(content=b"pretend firmware", board=Board.SIMULATED).is_valid())
        self.assertTrue(self.form(version="1.1.0", user=self.budi).is_valid())


class StartUpdateTests(OtaData):
    def test_queues_command_with_one_time_link(self):
        update = self.start()
        self.assertEqual((update.status, update.previous_version), (OtaStatus.QUEUED, "1.0.0"))
        command = update.command
        self.assertEqual(command.method, "ota")
        self.assertEqual(command.params, {
            "update": update.pk, "title": "Greenhouse firmware", "version": "1.1.0",
            "url": f"{BASE}/api/v1/ota/{update.download_token}.bin", "md5": IMAGE_MD5, "size": len(IMAGE),
        })
        self.assertNotIn(self.device.access_token, command.params["url"])
        self.assertEqual(self.statuses(update), ["QUEUED"])
        # Command ini dikirim seperti command lain (MQTT/CoAP mengambilnya dari sini).
        self.assertEqual(control.pending_commands(self.device), [command])

    def test_refusals(self):
        other_board = self.make_package(self.siti, "1.1.0", board=Board.ESP32S3)
        with self.assertRaisesMessage(services.OtaError, "for ESP32-S3, but the device is ESP32"):
            self.start(other_board)
        same = self.make_package(self.siti, "1.0.0")
        with self.assertRaisesMessage(services.OtaError, "already runs version 1.0.0"):
            self.start(same)
        self.start()
        with self.assertRaisesMessage(services.OtaError, "still running"):
            self.start()
        unknown = Device.objects.create(owner=self.siti, name="New board")
        with self.assertRaisesMessage(services.OtaError, "board is unknown"):
            self.start(device=unknown)

    def test_compatible_packages(self):
        self.make_package(self.siti, "1.0.0")
        self.make_package(self.siti, "2.0.0", board=Board.ESP32S3)
        packages = services.compatible_packages(FirmwarePackage.objects.all(), self.device)
        self.assertEqual([p.version for p in packages], ["1.1.0"])


class DownloadTests(OtaData):
    def url(self, update):
        return reverse("api:ota_download", args=[update.download_token])

    def test_download_once(self):
        update = self.start()
        response = self.client.get(self.url(update), REMOTE_ADDR="192.168.1.23")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), IMAGE)
        self.assertEqual(response["x-MD5"], IMAGE_MD5)
        self.assertEqual(response["Content-Length"], str(len(IMAGE)))
        update.refresh_from_db()
        self.assertEqual((update.status, update.downloaded_by), (OtaStatus.DOWNLOADING, "192.168.1.23"))
        again = self.client.get(self.url(update))
        self.assertEqual(again.status_code, 410)
        self.assertIn("already used", again.json()["error"])

    def test_unknown_expired_and_finished_links(self):
        self.assertEqual(self.client.get(reverse("api:ota_download", args=["nope"])).status_code, 404)
        update = self.start()
        OtaUpdate.objects.filter(pk=update.pk).update(token_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertIn("expired", self.client.get(self.url(update)).json()["error"])
        OtaUpdate.objects.filter(pk=update.pk).update(status=OtaStatus.CANCELED)
        self.assertEqual(self.client.get(self.url(update)).status_code, 410)


class ReportTests(OtaData):
    def test_steps_then_version_marks_updated(self):
        update = self.start()
        self.report({"update": update.pk, "state": "DOWNLOADING", "progress": 30})
        self.report({"update": update.pk, "state": "DOWNLOADING", "progress": 60})
        update.refresh_from_db()
        self.assertEqual((update.status, update.progress), (OtaStatus.DOWNLOADING, 60))
        for state in ("DOWNLOADED", "VERIFIED", "UPDATING"):
            self.report({"update": update.pk, "state": state})
        result = self.report({"version": "1.1.0", "board": "esp32"})
        self.assertIn("UPDATED", result)
        update.refresh_from_db()
        self.assertEqual(update.status, OtaStatus.UPDATED)
        self.assertIsNotNone(update.finished_at)
        self.assertEqual(self.statuses(update),
                         ["QUEUED", "DOWNLOADING", "DOWNLOADED", "VERIFIED", "UPDATING", "UPDATED"])
        self.device.refresh_from_db()
        self.assertEqual(self.device.firmware_version, "1.1.0")
        self.assertEqual([label for label, state in update.steps() if state == "done"][-1], "Updated")

    def test_rollback_is_a_failure(self):
        update = self.start()
        self.report({"update": update.pk, "state": "UPDATING"})
        self.report({"version": "1.0.0"})
        update.refresh_from_db()
        self.assertEqual(update.status, OtaStatus.FAILED)
        self.assertIn("restarted with version 1.0.0, not 1.1.0", update.error)

    def test_version_report_before_updating_does_not_fail(self):
        update = self.start()
        self.report({"version": "1.0.0"})  # mis. device tersambung ulang ke broker
        update.refresh_from_db()
        self.assertEqual(update.status, OtaStatus.QUEUED)

    def test_invalid_reports(self):
        update = self.start()
        self.report({"update": update.pk, "state": "VERIFIED"})
        cases = [
            ({"update": update.pk, "state": "DOWNLOADING"}, "cannot go back"),
            ({"update": update.pk, "state": "UPDATED"}, '"state" must be one of'),
            ({"update": 999, "state": "DOWNLOADING"}, "does not exist for this device"),
            ({"update": update.pk, "state": "VERIFIED", "progress": 150}, "0 to 100"),
            ({"version": "1.0 beta"}, '"version" must be text'),
            ({"version": "1.2.0", "board": "arduino"}, '"board" must be one of'),
            ({"hello": 1}, 'needs "version"'),
            ([1, 2], "JSON object"),
        ]
        for data, message in cases:
            with self.subTest(data=data), self.assertRaisesMessage(services.PayloadError, message):
                self.report(data)

    def test_another_device_cannot_report_for_this_update(self):
        update = self.start()
        other = Device.objects.create(owner=self.budi, name="Other", board=Board.ESP32)
        with self.assertRaisesMessage(services.PayloadError, "does not exist for this device"):
            self.report({"update": update.pk, "state": "FAILED"}, device=other)

    def test_device_reported_failure(self):
        update = self.start()
        self.report({"update": update.pk, "state": "FAILED", "error": "MD5 mismatch"})
        update.refresh_from_db()
        self.assertEqual((update.status, update.error), (OtaStatus.FAILED, "Device: MD5 mismatch"))
        with self.assertRaisesMessage(services.PayloadError, "already failed"):
            self.report({"update": update.pk, "state": "DOWNLOADED"})

    def test_command_response(self):
        update = self.start()
        control.record_command_result(self.device, update.command.pk, {"success": True})
        self.assertEqual(update.events.last().message, "The device accepted the update command")
        second = self.make_package(self.siti, "1.2.0")
        services.cancel(update, self.siti)
        refused = self.start(second)
        control.record_command_result(self.device, refused.command.pk, {"success": False, "error": "Low battery"})
        refused.refresh_from_db()
        self.assertEqual(refused.status, OtaStatus.FAILED)
        self.assertIn("Low battery", refused.error)

    def test_live_event(self):
        update = self.start()
        with mock.patch("ota.services.events.emit") as emit:
            self.report({"update": update.pk, "state": "DOWNLOADING", "progress": 10})
        device_id, payload = emit.call_args.args
        self.assertEqual(device_id, self.device.pk)
        self.assertEqual(payload, {"type": "ota", "update": update.pk, "status": "DOWNLOADING",
                                   "status_label": "Downloading", "progress": 10, "final": False, "error": ""})


class ProtocolTests(OtaData):
    def test_http(self):
        url = reverse("api:firmware", args=[self.device.access_token])
        response = self.client.post(url, json.dumps({"version": "1.0.1", "board": "esp32"}),
                                    content_type="application/json")
        self.assertEqual(response.json(), {"ok": True, "result": "running 1.0.1"})
        bad = self.client.post(url, json.dumps({"version": ""}), content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.post(reverse("api:firmware", args=["nope"]), "{}",
                                          content_type="application/json").status_code, 401)

    def test_mqtt(self):
        update = self.start()
        topic = f"p/{self.device.access_token}/firmware"
        result = handle_message(topic, json.dumps({"update": update.pk, "state": "DOWNLOADING"}).encode(), "p")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, f"firmware: update {update.pk}: DOWNLOADING")
        update.refresh_from_db()
        self.assertEqual(update.events.last().source, "MQTT")

    def test_coap(self):
        reply = coap.handle("POST", [self.device.access_token, "firmware"], b'{"version": "1.0.2"}')
        self.assertEqual((reply.code, reply.body), (coap.CHANGED, {"ok": True, "result": "running 1.0.2"}))
        self.assertEqual(coap.handle("GET", [self.device.access_token, "firmware"]).code, coap.METHOD_NOT_ALLOWED)


class TimeoutAndCancelTests(OtaData):
    def test_device_never_picks_up_the_command(self):
        update = self.start()
        update.command.expires_at = timezone.now() - timedelta(seconds=1)
        update.command.save()
        self.assertEqual(services.expire_due(), 1)
        update.refresh_from_db()
        self.assertEqual(update.status, OtaStatus.FAILED)
        self.assertIn("did not pick up", update.error)

    def test_device_does_not_come_back(self):
        update = self.start()
        self.report({"update": update.pk, "state": "UPDATING"})
        later = timezone.now() + timedelta(seconds=901)
        self.assertEqual(services.expire_due(now=later), 1)
        update.refresh_from_db()
        self.assertIn("did not come back with version 1.1.0 within 15 minutes", update.error)

    def test_cancel(self):
        update = self.start()
        services.cancel(update, self.siti)
        update.refresh_from_db()
        update.command.refresh_from_db()
        self.assertEqual((update.status, update.command.status), (OtaStatus.CANCELED, CommandStatus.EXPIRED))
        response = self.client.get(reverse("api:ota_download", args=[update.download_token]))
        self.assertEqual(response.status_code, 410)
        with self.assertRaisesMessage(services.OtaError, "already finished"):
            services.cancel(update, self.siti)

    def test_reach(self):
        self.assertEqual(services.reach("http://127.0.0.1:8000"), "loopback")
        self.assertEqual(services.reach("http://localhost:8000"), "loopback")
        self.assertEqual(services.reach(BASE), "private")
        self.assertEqual(services.reach("https://iot.example.com"), "unknown")


class OtaViewTests(OtaData):
    def setUp(self):
        self.client.force_login(self.siti)

    def test_device_page_and_start(self):
        page = self.client.get(reverse("devices:detail", args=[self.device.pk]))
        self.assertContains(page, 'id="firmware"')
        self.assertContains(page, "1.1.0 — Greenhouse firmware")
        self.assertNotContains(page, "only works on this computer")
        # Dibuka lewat 127.0.0.1: ESP32 tidak bisa mengunduh dari alamat itu.
        local = self.client.get(reverse("devices:detail", args=[self.device.pk]), HTTP_HOST="127.0.0.1:8000")
        self.assertContains(local, "only works on this computer")
        response = self.client.post(reverse("ota:start", args=[self.device.pk]), {"package": self.package.pk})
        self.assertRedirects(response, reverse("devices:detail", args=[self.device.pk]) + "#firmware",
                             fetch_redirect_response=False)
        update = OtaUpdate.objects.get()
        self.assertTrue(update.command.params["url"].startswith("http://testserver/api/v1/ota/"))
        page = self.client.get(reverse("devices:detail", args=[self.device.pk]))
        self.assertContains(page, f'data-ota-update="{update.pk}"')
        self.assertContains(page, "Waiting for the device to pick up the command")
        detail = self.client.get(reverse("ota:update_detail", args=[update.pk]))
        self.assertContains(detail, "queued by siti")
        self.client.post(reverse("ota:cancel", args=[update.pk]))
        update.refresh_from_db()
        self.assertEqual(update.status, OtaStatus.CANCELED)

    @override_settings(OTA_BASE_URL="http://192.168.1.10:8000")
    def test_base_url_setting(self):
        self.client.post(reverse("ota:start", args=[self.device.pk]), {"package": self.package.pk})
        self.assertTrue(OtaUpdate.objects.get().command.params["url"].startswith(BASE + "/api/v1/ota/"))

    def test_upload_list_and_delete(self):
        response = self.client.post(reverse("ota:package_upload"), {
            "title": "Greenhouse firmware", "version": "1.2.0", "board": "esp32",
            "file": SimpleUploadedFile("fw.bin", IMAGE),
        })
        package = FirmwarePackage.objects.get(version="1.2.0")
        self.assertRedirects(response, reverse("ota:package_detail", args=[package.pk]))
        self.assertContains(self.client.get(reverse("ota:package_list")), "1.2.0")
        path = package.file.path
        self.client.post(reverse("ota:package_delete", args=[package.pk]))
        self.assertFalse(FirmwarePackage.objects.filter(pk=package.pk).exists())
        import os
        self.assertFalse(os.path.exists(path), "the .bin is removed too")
        # Paket yang pernah dipakai disimpan untuk riwayat.
        self.start()
        response = self.client.post(reverse("ota:package_delete", args=[self.package.pk]), follow=True)
        self.assertContains(response, "kept for their history")

    def test_other_users(self):
        self.client.force_login(self.budi)
        self.assertEqual(self.client.get(reverse("ota:package_detail", args=[self.package.pk])).status_code, 404)
        update = self.start()
        self.assertEqual(self.client.get(reverse("ota:update_detail", args=[update.pk])).status_code, 404)
        own = Device.objects.create(owner=self.budi, name="Budi node", board=Board.ESP32)
        self.client.post(reverse("ota:start", args=[own.pk]), {"package": self.package.pk})
        self.assertFalse(own.ota_updates.exists(), "cannot use someone else's firmware")
        self.assertEqual(self.client.post(reverse("ota:start", args=[self.device.pk]),
                                          {"package": self.package.pk}).status_code, 404)


class FullFlowTests(OtaData):
    """Seperti device sungguhan: ambil command, jawab, unduh, lapor, restart, lapor versi."""

    def test_happy_path(self):
        update = self.start()
        [command] = control.pending_commands(self.device)
        control.mark_sent([command])
        params = command.params
        control.record_command_result(self.device, command.pk, {"success": True})
        path = params["url"].replace(BASE, "")
        response = self.client.get(path)
        body = b"".join(response.streaming_content)
        self.assertEqual(hashlib.md5(body).hexdigest(), params["md5"])
        for state in ("DOWNLOADED", "VERIFIED", "UPDATING"):
            self.report({"update": params["update"], "state": state, "progress": 100})
        self.report({"version": params["version"]})
        update.refresh_from_db()
        self.assertEqual(update.status, OtaStatus.UPDATED)
        self.assertEqual(self.statuses(update),
                         ["QUEUED", "QUEUED", "DOWNLOADING", "DOWNLOADED", "VERIFIED", "UPDATING", "UPDATED"])
