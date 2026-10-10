"""
Dashboard: semua variabel dalam bentuk card, dikelompokkan

    Lokasi
      └─ Device
           └─ card variabel, card variabel, ...

Pengguna bisa menyembunyikan lokasi, device, atau variabel tertentu lewat
halaman Customize (DashboardSettingsView). Pengaturannya disimpan per
pengguna di model DashboardPreference.
"""
from collections import defaultdict
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Max, Sum
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView

from devices.models import Telemetry, Variable
from devices.views import visible_devices, with_latest_values
from realtime.routing import DASHBOARD_WS_PATH

from .models import DashboardPreference

# Device dianggap "online" bila mengirim data dalam rentang waktu ini.
ONLINE_WINDOW = timedelta(minutes=5)


def device_tree(user):
    """Semua lokasi -> device -> variabel yang BOLEH dilihat `user`.

    Belum memperhatikan pengaturan tampil/sembunyi; itu urusan pemanggil.
    Mengembalikan list kelompok, urut nama lokasi; "No location" paling akhir:
        [{"location": Location|None, "devices": [Device, ...]}, ...]
    Setiap Device diberi atribut tambahan:
        .dash_variables  list Variable (dengan last_value, last_seen)
        .last_seen       waktu data terakhir dari variabel mana pun
    """
    devices = list(visible_devices(user).order_by("name"))
    # Satu query untuk semua variabel; "kind" urut abjad -> ACTUATOR dulu, lalu SENSOR.
    variables = with_latest_values(
        Variable.objects.filter(device__in=[d.pk for d in devices])
    ).order_by("kind", "key")

    by_device = defaultdict(list)
    for variable in variables:
        by_device[variable.device_id].append(variable)

    groups = {}
    for device in devices:
        device.dash_variables = by_device[device.pk]
        seen = [v.last_seen for v in device.dash_variables if v.last_seen]
        device.last_seen = max(seen) if seen else None
        group = groups.setdefault(device.location_id, {"location": device.location, "devices": []})
        group["devices"].append(device)

    return sorted(
        groups.values(),
        key=lambda g: (g["location"] is None, g["location"].name.lower() if g["location"] else ""),
    )


def dashboard_version(user):
    """Versi data seluruh dashboard; dipakai pemeriksaan cadangan (polling)."""
    devices = visible_devices(user)
    last = Telemetry.objects.filter(variable__device__in=devices).aggregate(m=Max("pk"))["m"]
    desired = Variable.objects.filter(device__in=devices).aggregate(s=Sum("desired_version"))["s"]
    return f"{last or 0}-{desired or 0}"


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        preference = DashboardPreference.for_user(user)
        hidden = preference.hidden_ids()
        now = timezone.now()

        groups = []
        shown_variables = set()
        all_variables = set()
        online = shown_devices = 0
        for group in device_tree(user):
            location = group["location"]
            for device in group["devices"]:
                all_variables.update(v.pk for v in device.dash_variables)
            location_hidden = (
                preference.hide_unlocated if location is None else location.pk in hidden["locations"]
            )
            if location_hidden:
                continue
            devices = []
            for device in group["devices"]:
                if device.pk in hidden["devices"]:
                    continue
                device.shown_variables = [
                    v for v in device.dash_variables if v.pk not in hidden["variables"]
                ]
                device.online = bool(device.last_seen and now - device.last_seen <= ONLINE_WINDOW)
                online += device.online
                shown_devices += 1
                shown_variables.update(v.pk for v in device.shown_variables)
                devices.append(device)
            if devices:
                groups.append({"location": location, "devices": devices})

        context["groups"] = groups
        context["stats"] = {
            "locations": len(groups),
            "devices": shown_devices,
            "online": online,
            "variables": len(shown_variables),
        }
        context["hidden_count"] = len(all_variables - shown_variables)
        context["has_any_device"] = bool(all_variables) or visible_devices(user).exists()
        # Untuk JavaScript: kabar tentang variabel ini diabaikan (sengaja tidak tampil).
        context["live_config"] = {"hiddenVariables": sorted(all_variables - shown_variables)}
        # Pembaruan langsung: WebSocket untuk semua device, polling sebagai cadangan.
        context["ws_path"] = DASHBOARD_WS_PATH
        context["updates_url"] = reverse("dashboard:updates")
        context["data_version"] = dashboard_version(user)
        return context


def ids_from(values):
    """["3", "x", "7"] -> {3, 7}. Nilai yang bukan angka diabaikan."""
    return {int(v) for v in values if v.isdigit()}


class DashboardSettingsView(LoginRequiredMixin, TemplateView):
    """Halaman Customize: centang = tampil, kosong = disembunyikan."""

    template_name = "dashboard/settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        preference = DashboardPreference.for_user(self.request.user)
        context["groups"] = device_tree(self.request.user)
        context["hidden"] = preference.hidden_ids()
        context["hide_unlocated"] = preference.hide_unlocated
        return context

    def post(self, request):
        preference = DashboardPreference.for_user(request.user)
        # Hanya id milik pengguna ini yang diperhitungkan, apa pun yang dikirim browser.
        devices = list(visible_devices(request.user))
        device_ids = {d.pk for d in devices}
        location_ids = {d.location_id for d in devices if d.location_id}
        variable_ids = set(
            Variable.objects.filter(device__in=device_ids).values_list("pk", flat=True)
        )

        # Yang dicentang = ditampilkan; sisanya disimpan sebagai tersembunyi.
        preference.hidden_locations.set(location_ids - ids_from(request.POST.getlist("location")))
        preference.hidden_devices.set(device_ids - ids_from(request.POST.getlist("device")))
        preference.hidden_variables.set(variable_ids - ids_from(request.POST.getlist("variable")))
        preference.hide_unlocated = "unlocated" not in request.POST
        preference.save()

        messages.success(request, "Dashboard settings saved.")
        return redirect("dashboard:index")


def dashboard_updates(request):
    """GET /dashboard/updates/ -> {"version": "..."} untuk pemeriksaan cadangan."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not logged in."}, status=401)
    return JsonResponse({"version": dashboard_version(request.user)})
