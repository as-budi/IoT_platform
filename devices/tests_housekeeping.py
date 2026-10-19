"""Tes tahap 16: tabel ringkasan telemetri dan status online/offline device."""
from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from api.services import save_telemetry
from mqtt_bridge import topics
from mqtt_bridge.handlers import handle_message
from mqtt_bridge.management.commands.mqtt_accounts import acl_text

from . import presence, rollups
from .charts import chart_series
from .models import Device, DeviceStatusEvent, RollupProgress, Telemetry, TelemetryRollup, Variable

User = get_user_model()
MINUTE, HOUR = TelemetryRollup.Resolution.MINUTE, TelemetryRollup.Resolution.HOUR


def utc(day, hour, minute=0, second=0):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=dt_timezone.utc)


class Data(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.budi = User.objects.create_user("budi", password="rahasia-12345")
        cls.node = Device.objects.create(owner=cls.budi, name="Node")
        cls.temp = Variable.objects.create(device=cls.node, key="temperature")
        cls.lamp = Variable.objects.create(device=cls.node, key="lamp", value_type=Variable.ValueType.BOOLEAN)

    def add(self, variable, value, ts):
        Telemetry.objects.create(variable=variable, value=value, ts=ts)

    def rollup(self, variable, resolution, bucket):
        return TelemetryRollup.objects.get(variable=variable, resolution=resolution, bucket=bucket)


class RollupTests(Data):
    def test_minute_and_hour_rows(self):
        self.add(self.temp, 20, utc(19, 10, 0, 5))
        self.add(self.temp, 30, utc(19, 10, 0, 50))
        self.add(self.temp, 25, utc(19, 10, 1, 10))
        self.add(self.temp, 40, utc(19, 11, 30))
        result = rollups.update(utc(19, 12, 0, 30))
        self.assertEqual(result["from"], utc(19, 10, 0))
        first = self.rollup(self.temp, MINUTE, utc(19, 10, 0))
        self.assertEqual((first.count, first.total, first.min_value, first.max_value), (2, 50, 20, 30))
        self.assertEqual(first.average, 25)
        self.assertEqual(self.rollup(self.temp, MINUTE, utc(19, 10, 1)).count, 1)
        ten = self.rollup(self.temp, HOUR, utc(19, 10))
        self.assertEqual((ten.count, ten.total, ten.min_value, ten.max_value), (3, 75, 20, 30))
        self.assertEqual(self.rollup(self.temp, HOUR, utc(19, 11)).max_value, 40)
        self.assertEqual(RollupProgress.objects.get(resolution=MINUTE).done_until, utc(19, 12, 0))
        self.assertEqual(RollupProgress.objects.get(resolution=HOUR).done_until, utc(19, 12))

    def test_running_bucket_is_not_summarised_yet(self):
        self.add(self.temp, 20, utc(19, 10, 5, 10))
        rollups.update(utc(19, 10, 5, 40))
        self.assertFalse(TelemetryRollup.objects.exists(), "10:05 is still running")
        rollups.update(utc(19, 10, 6, 1))
        self.assertTrue(TelemetryRollup.objects.filter(resolution=MINUTE, bucket=utc(19, 10, 5)).exists())
        self.assertFalse(TelemetryRollup.objects.filter(resolution=HOUR).exists(), "hour 10 is still running")

    def test_idempotent(self):
        self.add(self.temp, 20, utc(19, 10, 0, 5))
        rollups.update(utc(19, 11, 5))
        rollups.update(utc(19, 11, 5))
        rollups.update(utc(19, 11, 6))
        self.assertEqual(self.rollup(self.temp, MINUTE, utc(19, 10, 0)).count, 1)
        self.assertEqual(TelemetryRollup.objects.filter(resolution=HOUR).count(), 1)

    @override_settings(TELEMETRY_ROLLUP_LATE_SECONDS=600)
    def test_late_data_within_the_window_is_included(self):
        self.add(self.temp, 20, utc(19, 10, 30, 5))
        rollups.update(utc(19, 10, 35))
        self.add(self.temp, 40, utc(19, 10, 30, 30))   # tiba terlambat, 4 menit kemudian
        rollups.update(utc(19, 10, 36))
        self.assertEqual(self.rollup(self.temp, MINUTE, utc(19, 10, 30)).total, 60)

    @override_settings(TELEMETRY_ROLLUP_LATE_SECONDS=600)
    def test_very_late_data_needs_a_rebuild(self):
        self.add(self.temp, 20, utc(19, 9, 0, 5))
        rollups.update(utc(19, 10, 0, 30))
        self.add(self.temp, 40, utc(19, 9, 0, 30))     # terlambat 1 jam: di luar jendela 10 menit
        rollups.update(utc(19, 10, 1, 30))
        self.assertEqual(self.rollup(self.temp, MINUTE, utc(19, 9, 0)).count, 1)
        rollups.update(utc(19, 10, 2), since=utc(19, 9))
        self.assertEqual(self.rollup(self.temp, MINUTE, utc(19, 9, 0)).count, 2)
        self.assertEqual(self.rollup(self.temp, HOUR, utc(19, 9)).total, 60)

    def test_nothing_to_do(self):
        self.assertIsNone(rollups.update(utc(19, 10)))

    def test_command(self):
        self.add(self.temp, 20, timezone.now() - timedelta(days=2))
        out = StringIO()
        call_command("rollup_telemetry", "--rebuild", stdout=out)
        self.assertIn("1 minute row(s) and 1 hour row(s)", out.getvalue())
        call_command("rollup_telemetry", "--rebuild", "--since", "2026-01-01", stdout=out)


class RollupChartTests(Data):
    """Chart periode panjang dari tabel ringkasan = hasil yang sama dengan data asli."""

    def fill(self, end, days, step_minutes=7):
        rows, moment, value = [], end - timedelta(days=days), 0
        while moment < end:
            rows.append(Telemetry(variable=self.temp, value=value % 50, ts=moment))
            rows.append(Telemetry(variable=self.lamp, value=1 if value % 9 == 0 else 0, ts=moment))
            moment += timedelta(minutes=step_minutes)
            value += 1
        Telemetry.objects.bulk_create(rows)

    def compare(self, days, source):
        end = timezone.now().replace(microsecond=0)
        self.fill(end, days + 1)
        raw = chart_series(self.temp, end - timedelta(days=days), end)
        self.assertEqual(raw["source"], 0, "no summaries yet: raw data")
        rollups.update(end - timedelta(minutes=30))     # bagian terakhir belum diringkas
        for variable in (self.temp, self.lamp):
            summary = chart_series(variable, end - timedelta(days=days), end)
            self.assertEqual(summary["source"], source)
            self.assertGreater(len(summary["points"]), 10)
            # Total baris yang terwakili sama dengan menghitung data asli.
            expected = variable.telemetry.filter(ts__gte=end - timedelta(days=days), ts__lte=end).count()
            self.assertAlmostEqual(summary["rows"], expected, delta=expected * 0.01 + 2)

    def test_week_uses_minute_summaries(self):
        self.compare(7, MINUTE)

    def test_year_uses_hour_summaries(self):
        self.compare(30 * 2, HOUR)

    def test_values_match_raw_average(self):
        end = utc(19, 12)
        # Dua hari: 10 per jam bernilai sama dalam satu jam -> rata-rata per ember diketahui.
        for hour in range(48):
            start = end - timedelta(hours=48 - hour)
            for i in range(6):
                self.add(self.temp, hour, start + timedelta(minutes=i * 10))
        rollups.update(end)
        series = chart_series(self.temp, end - timedelta(days=2), end)
        self.assertEqual(series["source"], MINUTE)
        self.assertEqual({p["y"] for p in series["points"]}, set(range(48)))
        self.assertEqual(series["rows"], 48 * 6)

    def test_boolean_keeps_the_highest_value(self):
        end = utc(19, 12)
        for i in range(3 * 24):
            self.add(self.lamp, 1 if i == 5 else 0, end - timedelta(hours=72 - i))
        rollups.update(end)
        series = chart_series(self.lamp, end - timedelta(days=3), end)
        self.assertEqual(sorted({p["y"] for p in series["points"]}), [0, 1])

    def test_recent_data_comes_from_raw_telemetry(self):
        end = utc(19, 12)
        self.add(self.temp, 10, end - timedelta(days=2))
        rollups.update(end - timedelta(hours=1))
        self.add(self.temp, 99, end - timedelta(minutes=5))   # sesudah done_until
        series = chart_series(self.temp, end - timedelta(days=2, minutes=1), end)
        self.assertEqual([p["y"] for p in series["points"]], [10, 99])


@override_settings(DEVICE_OFFLINE_SECONDS=300)
class PresenceTests(Data):
    def test_telemetry_brings_a_device_online_and_silence_takes_it_offline(self):
        with mock.patch("realtime.events.status_changed") as notify:
            save_telemetry(self.node, [("temperature", 21.0, timezone.now())])
        self.node.refresh_from_db()
        self.assertTrue(self.node.is_online)
        notify.assert_called_once()
        self.assertEqual(DeviceStatusEvent.objects.get().reason, "Data received")

        self.assertEqual(presence.check_timeouts(timezone.now() + timedelta(seconds=200)), 0)
        self.assertEqual(presence.check_timeouts(timezone.now() + timedelta(seconds=301)), 1)
        self.node.refresh_from_db()
        self.assertFalse(self.node.is_online)
        self.assertEqual(DeviceStatusEvent.objects.first().reason, "No data for 300 s")
        self.assertEqual(presence.check_timeouts(timezone.now() + timedelta(seconds=400)), 0, "only once")

    def test_second_message_does_not_log_again(self):
        save_telemetry(self.node, [("temperature", 21.0, timezone.now())])
        self.node.refresh_from_db()
        save_telemetry(self.node, [("temperature", 22.0, timezone.now())])
        self.assertEqual(DeviceStatusEvent.objects.count(), 1)

    def test_mqtt_session_and_last_will(self):
        prefix = "demo"
        topic = topics.status(prefix, self.node.access_token)
        self.assertEqual(topics.parse(topic, prefix), (topics.STATUS, self.node.access_token, None))
        self.assertIn(f"{prefix}/+/status", topics.subscriptions(prefix))
        self.assertTrue(handle_message(topic, b"online", prefix).ok)
        self.node.refresh_from_db()
        self.assertTrue(self.node.is_online and self.node.mqtt_connected)
        # Sesi MQTT terbuka: diam lama pun tetap online (broker yang mendeteksi putus).
        self.assertEqual(presence.check_timeouts(timezone.now() + timedelta(hours=2)), 0)
        result = handle_message(topic, b"offline", prefix)          # Last Will dari broker
        self.assertEqual(result.message, "status: offline")
        self.node.refresh_from_db()
        self.assertFalse(self.node.is_online or self.node.mqtt_connected)
        self.assertEqual(DeviceStatusEvent.objects.first().reason, "MQTT session ended (disconnect or Last Will)")
        self.assertFalse(handle_message(topic, b"maybe", prefix).ok)

    def test_parse_status(self):
        for payload, expected in [(b"online", True), (b" OFFLINE\n", False), (b'"online"', True),
                                  (b'{"online": false}', False), (b"lost", False), (b"{bad", None), (b"\xff", None)]:
            self.assertIs(presence.parse_status(payload), expected, payload)

    def test_acl_allows_the_status_topic(self):
        self.assertIn("pattern write demo/%u/status", acl_text("demo", "platform"))

    def test_device_pages_show_the_status(self):
        self.client.force_login(self.budi)
        save_telemetry(self.node, [("temperature", 21.0, timezone.now())])
        page = self.client.get(reverse("devices:detail", args=[self.node.pk]))
        self.assertContains(page, "Connection history")
        self.assertContains(page, '<span class="status-pill online"', count=2)   # judul + riwayat
        self.assertContains(page, topics.status("", self.node.access_token).lstrip("/"))
        listing = self.client.get(reverse("devices:list"))
        self.assertContains(listing, "status-pill online")

    def test_housekeeping_once(self):
        Device.objects.filter(pk=self.node.pk).update(is_online=True, last_seen_at=timezone.now() - timedelta(hours=1))
        self.add(self.temp, 20, timezone.now() - timedelta(hours=2))
        call_command("housekeeping", "--once", stdout=StringIO())
        self.node.refresh_from_db()
        self.assertFalse(self.node.is_online)
        self.assertTrue(TelemetryRollup.objects.exists())
