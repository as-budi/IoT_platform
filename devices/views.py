import ipaddress

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core.paginator import Paginator
from django.db.models import Count, F, Func, IntegerField, Max, OuterRef, Subquery, Sum
from django.db.models.deletion import ProtectedError, RestrictedError
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
    View,
)

from mqtt_bridge import topics as mqtt_topics
from realtime import events
from realtime.routing import device_ws_path
from sharing import access

from .charts import CUSTOM, PERIODS, chart_series, describe_seconds
from .forms import (
    ChartPeriodForm,
    CommandForm,
    DeviceForm,
    LocationForm,
    SetDesiredForm,
    VariableForm,
)
from .models import Command, Device, Location, Telemetry, Variable

HISTORY_PAGE_SIZE = 25
# Periode chart dan peringkasan titiknya ada di devices/charts.py.
# Jumlah command terakhir yang ditampilkan di halaman device.
RECENT_COMMANDS = 20
RECENT_STATUS_EVENTS = 10


# --- Aturan kepemilikan ------------------------------------------------------

def visible_devices(user, level=access.VIEWER):
    """Device yang boleh dilihat `user`: Admin semua; pengguna lain miliknya
    DAN yang dibagikan kepadanya (tahap 19).

    `level` menaikkan syaratnya, mis. visible_devices(user, access.OPERATOR) =
    device yang boleh dikontrol. Aturannya sendiri ditulis di SATU tempat,
    sharing/access.py; semua view device, variabel, dashboard, WebSocket, dan
    rule engine mengambil datanya lewat fungsi ini.
    """
    return access.devices_for(user, level).select_related("owner", "location__owner")


def visible_locations(user):
    """Lokasi yang boleh dilihat `user`: Admin semua, User hanya miliknya (tahap 12).

    Pasangan visible_devices() untuk lokasi; semua view lokasi memakai fungsi ini.
    """
    queryset = Location.objects.select_related("owner")
    if user.is_admin:
        return queryset
    return queryset.filter(owner=user)


def count_rows(telemetry):
    """Bungkus queryset telemetri (yang memakai OuterRef) menjadi Subquery hitungan.

    Untuk setiap baris query utama, database menjalankan kira-kira:
        SELECT COUNT(id) FROM devices_telemetry WHERE <syarat queryset>
    Func(..., function="COUNT") dipakai, bukan Count(), karena Count()
    adalah "aggregate" yang membuat Django menambahkan GROUP BY. Di sini
    kita cukup satu angka per baris, tanpa pengelompokan.
    """
    counted = telemetry.order_by().annotate(total=Func(F("pk"), function="COUNT")).values("total")
    return Coalesce(Subquery(counted, output_field=IntegerField()), 0)


def with_latest_values(variables):
    """Tambahkan last_value, last_seen, dan telemetry_count ke tiap variabel."""
    rows = Telemetry.objects.filter(variable=OuterRef("pk"))
    newest = rows.order_by("-ts")
    return variables.annotate(
        last_value=Subquery(newest.values("value")[:1]),
        last_seen=Subquery(newest.values("ts")[:1]),
        telemetry_count=count_rows(rows),
    )


def data_version(device, variable=None):
    """"Sidik jari" data sebuah device (atau satu variabelnya), berupa teks pendek.

    Nilainya berubah setiap kali ada sesuatu yang baru:
    - telemetri baru  -> id terbesar di tabel Telemetry bertambah
    - desired state   -> jumlah desired_version bertambah
    - command         -> id command terbaru, atau waktu dikirim/selesai, berubah
    Halaman cukup membandingkan teks ini dengan yang ia terima sebelumnya;
    bila berbeda, halaman dimuat ulang (lihat static/js/auto_refresh.js).
    Ini jauh lebih ringan daripada mengirim ulang seluruh data setiap beberapa detik.
    """
    telemetry = Telemetry.objects.filter(variable__device=device)
    variables = device.variables.all()
    if variable is not None:
        telemetry = telemetry.filter(variable=variable)
        variables = variables.filter(pk=variable.pk)

    parts = [
        telemetry.aggregate(last=Max("pk"))["last"],
        variables.aggregate(total=Sum("desired_version"))["total"],
    ]
    if variable is None:
        commands = device.commands.aggregate(
            last=Max("pk"), sent=Max("sent_at"), done=Max("completed_at")
        )
        # Pembaruan firmware (tahap 9): status/progres terakhir, dan versi yang dilaporkan.
        ota = device.ota_updates.aggregate(last=Max("updated_at"))["last"]
        parts += [
            commands["last"],
            commands["sent"] and commands["sent"].timestamp(),
            commands["done"] and commands["done"].timestamp(),
            ota and ota.timestamp(),
            device.firmware_reported_at and device.firmware_reported_at.timestamp(),
            # Status online/offline (tahap 16).
            device.status_changed_at and device.status_changed_at.timestamp(),
        ]
    return "-".join(str(part or 0) for part in parts)


# --- Device -----------------------------------------------------------------

class OwnedDeviceMixin(LoginRequiredMixin):
    """Membatasi view device ke device yang boleh dilihat pengguna.

    Pengguna biasa yang menebak URL device orang lain mendapat 404, karena
    device itu "tidak ada" di queryset-nya. Device yang TERLIHAT (dibagikan)
    tetapi perannya kurang dari `access_level` mendapat 403 (tahap 19).
    """

    access_level = access.VIEWER

    def get_queryset(self):
        return visible_devices(self.request.user)

    def get_object(self, queryset=None):
        device = super().get_object(queryset)
        self.access_role = access.require(self.request.user, device, self.access_level)
        return device


def with_device_summary(devices):
    """Tambahkan variabel (prefetch), last_seen, dan telemetry_count ke tiap device.

    Dipakai daftar device dan halaman lokasi, supaya tabelnya sama persis.
    """
    # Telemetri milik device ini = telemetri dari semua variabelnya.
    # Dua garis bawah (variable__device) berarti "ikuti relasi":
    # telemetry -> variable -> device.
    rows = Telemetry.objects.filter(variable__device=OuterRef("pk"))
    return (
        devices
        # prefetch_related mengambil variabel SEMUA device dalam satu
        # query tambahan, bukan satu query per device di template.
        .prefetch_related("variables")
        .annotate(
            last_seen=Subquery(rows.order_by("-ts").values("ts")[:1]),
            telemetry_count=count_rows(rows),
        )
    )


class DeviceListView(OwnedDeviceMixin, ListView):
    template_name = "devices/device_list.html"
    context_object_name = "devices"

    # Nilai khusus di ?location= untuk "device tanpa lokasi".
    NO_LOCATION = "none"

    def get_queryset(self):
        devices = with_device_summary(super().get_queryset())
        # Saring berdasarkan lokasi lewat parameter URL, mis. ?location=3
        selected = self.request.GET.get("location", "")
        if selected == self.NO_LOCATION:
            devices = devices.filter(location__isnull=True)
        elif selected.isdigit():
            devices = devices.filter(location_id=int(selected))
        # Tahap 19: ?access=mine (milik saya) atau ?access=shared (dibagikan kepada saya).
        scope = self.request.GET.get("access", "")
        if scope == "mine":
            devices = devices.filter(owner=self.request.user)
        elif scope == "shared":
            devices = devices.exclude(owner=self.request.user)
        return devices

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["locations"] = visible_locations(user)
        context["selected_location"] = self.request.GET.get("location", "")
        context["no_location"] = self.NO_LOCATION
        context["selected_access"] = self.request.GET.get("access", "")
        # Lencana "Shared by siti · Operator" untuk device orang lain (bukan untuk Admin).
        if not user.is_admin:
            roles = access.remember(self.request, context["devices"])
            for device in context["devices"]:
                device.shared_role = None if device.owner_id == user.pk else access.LABEL.get(roles.get(device.pk))
        context["has_shared"] = not user.is_admin and access.shares_of(user).exists()
        return context


class DeviceDetailView(OwnedDeviceMixin, DetailView):
    """Tampilan per device: kontrol aktuator, card sensor, dan daftar command."""

    template_name = "devices/device_detail.html"
    context_object_name = "device"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        device = self.object
        # Tahap 19: tombol yang tampil mengikuti peran (pemeriksaan sebenarnya di view POST).
        role = self.access_role
        access.request_cache(self.request)[device.pk] = role
        context.update(
            can_operate=access.has(role, access.OPERATOR),
            can_edit=access.has(role, access.EDITOR),
            is_owner=role == access.OWNER,
            # Tautan lokasi hanya untuk yang boleh membuka halaman lokasinya.
            location_visible=bool(device.location_id) and visible_locations(self.request.user).filter(
                pk=device.location_id).exists(),
        )
        from sharing.views import device_sharing_context

        context.update(device_sharing_context(self.request, device, role))
        variables = list(with_latest_values(device.variables.all()))
        # Dipisah di Python (bukan dua query) karena daftarnya sudah di tangan.
        context["actuators"] = [v for v in variables if v.is_actuator]
        context["sensors"] = [v for v in variables if not v.is_actuator]

        # Bereskan command yang kedaluwarsa dulu, supaya statusnya akurat.
        device.commands.expire_due()
        context["commands"] = device.commands.select_related("created_by")[:RECENT_COMMANDS]
        context["command_form"] = CommandForm()

        # Alamat lengkap endpoint telemetri device ini, untuk contoh curl/ESP32.
        # build_absolute_uri menambahkan http://host:port di depan path.
        context["telemetry_url"] = self.request.build_absolute_uri(
            reverse("api:telemetry", args=[device.access_token])
        )
        # Alamat broker dan topik MQTT device ini (lihat mqtt_bridge/topics.py).
        prefix, token = settings.MQTT_TOPIC_PREFIX, device.access_token
        context["mqtt"] = mqtt_info(device)
        context["mqtt"].update({
            "telemetry": mqtt_topics.telemetry(prefix, token),
            "desired": mqtt_topics.desired(prefix, token),
            "commands": mqtt_topics.command(prefix, token, "+"),
            "command_response": mqtt_topics.command_response(prefix, token, "<id>"),
            "errors": mqtt_topics.errors(prefix, token),
            "all": mqtt_topics.device_base(prefix, token) + "/#",
        })
        context["coap"] = coap_info(self.request, device)
        context["firmware_url"] = self.request.build_absolute_uri(
            reverse("api:firmware", args=[device.access_token])
        )
        context["mqtt"]["firmware"] = mqtt_topics.firmware(prefix, token)
        context["mqtt"]["status"] = mqtt_topics.status(prefix, token)
        # Kartu "Firmware" (tahap 9, OTA): versi, tombol update, progres, riwayat.
        from ota.views import device_firmware_context

        context.update(device_firmware_context(self.request, device))
        # Alarm aktif device ini (dibuat oleh rule engine, lihat app rules).
        context["active_alarms"] = device.alarms.active().select_related("rule", "acknowledged_by")
        # Riwayat online/offline (tahap 16, devices/presence.py).
        context["status_events"] = device.status_events.all()[:RECENT_STATUS_EVENTS]
        context["offline_after"] = settings.DEVICE_OFFLINE_SECONDS

        # Pembaruan langsung: WebSocket, dengan polling versi data sebagai cadangan.
        context["data_version"] = data_version(device)
        context["updates_url"] = reverse("devices:updates", args=[device.pk])
        context["ws_path"] = device_ws_path(device.pk)
        return context


def device_updates(request, pk):
    """GET /devices/<pk>/updates/[?variable=<id>] -> {"version": "..."} (JSON).

    Dipanggil JavaScript halaman device/variabel setiap beberapa detik
    ("polling"). Bila sesi login habis, balasannya JSON 401, bukan redirect
    ke halaman login, karena pemanggilnya adalah JavaScript, bukan manusia.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not logged in."}, status=401)
    device = get_object_or_404(visible_devices(request.user), pk=pk)
    variable = None
    variable_id = request.GET.get("variable", "")
    if variable_id:
        if not variable_id.isdigit():
            return JsonResponse({"error": "Invalid variable."}, status=400)
        variable = get_object_or_404(device.variables, pk=int(variable_id))
    return JsonResponse({"version": data_version(device, variable)})


def mqtt_info(device):
    """Alamat broker untuk DEVICE (bukan untuk mqtt_bridge), untuk tab MQTT.

    MQTT_PUBLIC_* di .env menentukan host/port/TLS yang ditulis di halaman;
    MQTT_DEVICE_LOGIN menentukan apakah device harus login dengan access token.
    "options" adalah opsi mosquitto_pub/mosquitto_sub yang sesuai, supaya contoh
    perintah di halaman bisa langsung disalin dan dijalankan.
    """
    token = device.access_token
    options = f"-h {settings.MQTT_PUBLIC_HOST} -p {settings.MQTT_PUBLIC_PORT}"
    if settings.MQTT_PUBLIC_TLS:
        # Percaya pada daftar CA sistem operasi (Let's Encrypt sudah ada di dalamnya).
        options += " --tls-use-os-certs"
    if settings.MQTT_DEVICE_LOGIN:
        options += f" -u {token} -P {token}"
    return {
        "host": settings.MQTT_PUBLIC_HOST,
        "port": settings.MQTT_PUBLIC_PORT,
        "tls": settings.MQTT_PUBLIC_TLS,
        "login": settings.MQTT_DEVICE_LOGIN,
        "options": options,
        # Bridge berjalan sebagai service di server (DEBUG=False); di laptop, manual.
        "show_bridge_hint": settings.DEBUG,
    }


def coap_info(request, device):
    """Alamat CoAP device ini, untuk tab CoAP di halaman device.

    Host diambil dari COAP_PUBLIC_HOST (.env); bila kosong, dari address bar
    browser. "reach" menjelaskan siapa yang bisa menjangkau alamat itu:
      loopback -> hanya komputer ini sendiri (127.0.0.1 tidak berguna bagi ESP32)
      private  -> hanya device di jaringan yang sama (192.168.x.x, 10.x.x.x, ...)
      public   -> dari mana saja, selama port UDP-nya terbuka
    """
    host = settings.COAP_PUBLIC_HOST or request.get_host()
    if not settings.COAP_PUBLIC_HOST:
        # Buang nomor port HTTP; perhatikan alamat IPv6 seperti [::1]:8000.
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 or "]:" in host else host
    bare = host.strip("[]")
    try:
        address = ipaddress.ip_address(bare)
        reach = "loopback" if address.is_loopback else "private" if address.is_private else "public"
    except ValueError:
        reach = "loopback" if bare == "localhost" else "unknown"
    if ":" in bare and not host.startswith("["):
        host = f"[{bare}]"  # alamat IPv6 di dalam URI harus diberi kurung siku
    base = f"coap://{host}:{settings.COAP_PORT}/api/v1/{device.access_token}"
    return {
        "host": host,
        "port": settings.COAP_PORT,
        "reach": reach,
        "configured": bool(settings.COAP_PUBLIC_HOST),
        "telemetry": f"{base}/telemetry",
        "desired": f"{base}/desired",
        "commands": f"{base}/commands",
        "response": f"{base}/commands/<id>/response",
        "firmware": f"{base}/firmware",
    }


class DeviceFormMixin(OwnedDeviceMixin, SuccessMessageMixin):
    """Bagian yang sama antara view tambah dan ubah device."""

    form_class = DeviceForm
    template_name = "devices/device_form.html"

    def get_form_kwargs(self):
        # Kirim pengguna yang login ke DeviceForm (lihat DeviceForm.__init__).
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_success_url(self):
        return reverse("devices:detail", args=[self.object.pk])


class DeviceCreateView(DeviceFormMixin, CreateView):
    success_message = "Device %(name)s was added."

    def form_valid(self, form):
        # Pengguna biasa tidak memilih pemilik: device otomatis jadi miliknya.
        if not self.request.user.is_admin:
            form.instance.owner = self.request.user
        return super().form_valid(form)


class DeviceUpdateView(DeviceFormMixin, UpdateView):
    access_level = access.EDITOR
    context_object_name = "device"
    success_message = "Device %(name)s was updated."


class DeviceDeleteView(OwnedDeviceMixin, SuccessMessageMixin, DeleteView):
    access_level = access.OWNER   # menghapus device: hanya pemilik dan Admin
    template_name = "devices/device_confirm_delete.html"
    context_object_name = "device"
    success_url = reverse_lazy("devices:list")
    success_message = "Device was deleted."


# --- Variabel ---------------------------------------------------------------

class OwnedVariableMixin(LoginRequiredMixin):
    """Dasar semua view variabel.

    URL variabel memuat <device_pk>. Device itu diambil lewat
    visible_devices(), jadi aturan kepemilikan otomatis ikut berlaku:
    device orang lain -> 404, dan variabel hanya dicari di device tersebut.
    """

    access_level = access.VIEWER

    def get_device(self):
        # Disimpan di atribut supaya query device tidak diulang-ulang.
        if not hasattr(self, "_device"):
            self._device = get_object_or_404(
                visible_devices(self.request.user), pk=self.kwargs["device_pk"]
            )
            # Tahap 19: terlihat tetapi perannya kurang -> 403.
            self.access_role = access.require(self.request.user, self._device, self.access_level)
            access.request_cache(self.request)[self._device.pk] = self.access_role
        return self._device

    def get_queryset(self):
        return self.get_device().variables.all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["device"] = self.get_device()
        context["can_operate"] = access.has(self.access_role, access.OPERATOR)
        context["can_edit"] = access.has(self.access_role, access.EDITOR)
        return context


class VariableDetailView(OwnedVariableMixin, DetailView):
    """Tampilan per variabel: nilai terkini, chart per periode, dan tabel history."""

    template_name = "devices/variable_detail.html"
    context_object_name = "variable"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        variable = self.object

        # 1) Card: nilai terkini.
        latest = variable.latest
        context["latest"] = latest

        # 2) Chart. Periode dibaca dari URL (?period=5m, ?period=custom&start=...).
        # Rentangnya berakhir di "sekarang", sehingga data yang baru masuk
        # langsung terlihat di ujung kanan chart.
        period_form = ChartPeriodForm(self.request.GET)
        period, start, end = period_form.get_window(timezone.now())
        series = chart_series(variable, start, end)
        # Titik {x: waktu dalam milidetik, y: nilai}, urut lama -> baru.
        context["chart_points"] = series["points"]
        context["chart"] = {
            "period": period,
            "label": PERIODS[period][0],
            "start": start,
            "end": end,
            "rows": series["rows"],
            "bucket": describe_seconds(series["bucket_seconds"]) if series["bucket_seconds"] else "",
            # Tahap 16: titik dibaca dari tabel ringkasan per menit/jam (0 = data asli).
            "source": describe_seconds(series["source"]) if series["source"] else "",
            # Batas sumbu x = seluruh periode, walau datanya hanya sebagian.
            "range": {"min": int(start.timestamp() * 1000), "max": int(end.timestamp() * 1000)},
        }
        context["period_form"] = period_form
        context["periods"] = [(key, label) for key, (label, _) in PERIODS.items()]
        context["custom_period"] = CUSTOM
        # Kotak custom terbuka bila dipilih, atau bila isiannya salah.
        context["custom_open"] = period == CUSTOM or self.request.GET.get("period") == CUSTOM
        # Isian awal kotak custom: nilai dari URL, atau rentang yang sedang tampil.
        context["custom_start"] = self.request.GET.get("start") or timezone.localtime(start).strftime("%Y-%m-%dT%H:%M")
        context["custom_end"] = self.request.GET.get("end", "")

        # 3) Tabel history. Paginator memotong data menjadi halaman-halaman
        # (?page=2, dst.) supaya ribuan baris tidak dimuat sekaligus.
        paginator = Paginator(variable.telemetry.all(), HISTORY_PAGE_SIZE)
        context["page_obj"] = paginator.get_page(self.request.GET.get("page"))

        # 4) Khusus aktuator: riwayat permintaan (siapa meminta apa, kapan).
        if variable.is_actuator:
            context["desired_log"] = variable.desired_log.select_related("changed_by")[:20]

        # 5) Pembaruan langsung (WebSocket + polling cadangan).
        device = self.get_device()
        context["data_version"] = data_version(device, variable)
        context["updates_url"] = reverse("devices:updates", args=[device.pk]) + f"?variable={variable.pk}"
        context["ws_path"] = device_ws_path(device.pk)
        # Pengaturan untuk JavaScript halaman ini (dikirim lewat json_script).
        context["live_config"] = {
            "variable": variable.pk,
            "boolean": variable.is_boolean,
            "unit": "" if variable.is_boolean else variable.unit,
            # Titik baru hanya ditambahkan ke chart bila chart menampilkan data
            # asli (bukan rata-rata) dan periodenya berakhir di "sekarang".
            "chartLive": not series["bucket_seconds"] and not context["custom_end"],
            "periodMs": int((end - start).total_seconds() * 1000),
            # Baris baru hanya disisipkan ke tabel di halaman pertama.
            "firstPage": context["page_obj"].number == 1,
            "pageSize": HISTORY_PAGE_SIZE,
        }
        return context


class VariableFormMixin(OwnedVariableMixin, SuccessMessageMixin):
    """Bagian yang sama antara view tambah dan ubah variabel."""

    access_level = access.EDITOR

    form_class = VariableForm
    template_name = "devices/variable_form.html"

    def get_form_kwargs(self):
        # VariableForm butuh device-nya untuk memeriksa key kembar.
        kwargs = super().get_form_kwargs()
        kwargs["device"] = self.get_device()
        return kwargs

    def get_success_url(self):
        return reverse("devices:variable_detail", args=[self.get_device().pk, self.object.pk])


class VariableCreateView(VariableFormMixin, CreateView):
    success_message = "Variable %(key)s was added."

    def form_valid(self, form):
        # Device tidak dipilih di form: diambil dari URL.
        form.instance.device = self.get_device()
        return super().form_valid(form)


class VariableUpdateView(VariableFormMixin, UpdateView):
    context_object_name = "variable"
    success_message = "Variable %(key)s was updated."


class VariableDeleteView(OwnedVariableMixin, SuccessMessageMixin, DeleteView):
    access_level = access.EDITOR
    template_name = "devices/variable_confirm_delete.html"
    context_object_name = "variable"
    success_message = "Variable was deleted."

    def get_success_url(self):
        return reverse("devices:detail", args=[self.get_device().pk])


# --- Kontrol: desired state & command ----------------------------------------

def redirect_back(request, fallback_url):
    """Kembali ke halaman asal (field `next` di form), bila alamatnya aman.

    url_has_allowed_host_and_scheme mencegah "open redirect": tanpa
    pemeriksaan ini, seseorang bisa membuat tautan yang mengarahkan pengguna
    ke situs lain setelah menekan tombol di aplikasi kita.
    """
    target = request.POST.get("next")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(target)
    return redirect(fallback_url)


class SetDesiredView(OwnedVariableMixin, View):
    """Pola 1 — mengubah desired state sebuah aktuator. Hanya menerima POST.

    View ini TIDAK mengubah reported state: itu tugas device. Ia hanya
    mencatat permintaan; status "Waiting for device" bertahan sampai device
    melaporkan nilai baru.
    """

    http_method_names = ["post"]
    access_level = access.OPERATOR

    def post(self, request, device_pk, pk):
        variable = get_object_or_404(
            self.get_queryset().filter(kind=Variable.Kind.ACTUATOR), pk=pk
        )
        form = SetDesiredForm(request.POST, variable=variable)
        if form.is_valid():
            variable = variable.set_desired(form.cleaned_data["value"], request.user)
            messages.success(request, f"{variable.display_name}: request sent, waiting for the device.")
        else:
            for error in form.errors.get("value", ["Invalid value."]):
                messages.error(request, f"{variable.display_name}: {error}")
        return redirect_back(request, reverse("devices:detail", args=[device_pk]))


class CommandCreateView(LoginRequiredMixin, View):
    """Pola 2 — membuat command baru untuk device. Hanya menerima POST."""

    http_method_names = ["post"]

    def post(self, request, device_pk):
        device = get_object_or_404(visible_devices(request.user), pk=device_pk)
        access.require(request.user, device, access.OPERATOR)
        form = CommandForm(request.POST)
        if form.is_valid():
            command = Command.objects.create(
                device=device,
                method=form.cleaned_data["method"],
                params=form.cleaned_data["params"],
                created_by=request.user,
                expires_at=timezone.now() + form.ttl_delta,
            )
            events.command_changed(command)
            messages.success(request, f"Command {command.method} queued.")
        else:
            # Form ini tidak dirender ulang (kita redirect), jadi error-nya
            # disampaikan lewat messages. form[field].label = label tampilan.
            for field, errors in form.errors.items():
                label = form[field].label if field in form.fields else "Command"
                for error in errors:
                    messages.error(request, f"{label}: {error}")
        return redirect(reverse("devices:detail", args=[device.pk]) + "#commands")


# --- Lokasi -------------------------------------------------------------------
#
# Sejak tahap 12 lokasi punya pemilik, sama seperti device. Setiap pengguna
# menambah, mengubah, dan menghapus lokasinya sendiri; lokasi orang lain
# "tidak ada" baginya (404). Admin melihat dan mengelola semua lokasi.

def location_points(locations):
    """Ubah lokasi menjadi list dict sederhana untuk peta (dikirim lewat json_script)."""
    return [
        {
            "name": location.name,
            "lat": float(location.latitude),
            "lng": float(location.longitude),
            "url": reverse("devices:location_detail", args=[location.pk]),
        }
        for location in locations
    ]


class OwnedLocationMixin(LoginRequiredMixin):
    """Membatasi view lokasi ke lokasi yang boleh dilihat (lihat visible_locations)."""

    def get_queryset(self):
        return visible_locations(self.request.user)


class LocationListView(OwnedLocationMixin, ListView):
    template_name = "devices/location_list.html"
    context_object_name = "locations"

    def get_queryset(self):
        # Device di sebuah lokasi selalu milik pemilik lokasi itu (dijaga
        # DeviceForm), jadi jumlah device tidak perlu disaring lagi.
        return super().get_queryset().annotate(device_count=Count("devices"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["map_points"] = location_points(context["locations"])
        return context


class LocationDetailView(OwnedLocationMixin, DetailView):
    """Tampilan per lokasi: peta dan device (yang boleh dilihat) di lokasi itu."""

    template_name = "devices/location_detail.html"
    context_object_name = "location"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["devices"] = with_device_summary(
            visible_devices(self.request.user).filter(location=self.object)
        )
        context["map_points"] = location_points([self.object])
        return context


class LocationFormMixin(OwnedLocationMixin, SuccessMessageMixin):
    form_class = LocationForm
    template_name = "devices/location_form.html"

    def get_form_kwargs(self):
        # Kirim pengguna yang login ke LocationForm: pemilik lokasi baru.
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_success_url(self):
        return reverse("devices:location_detail", args=[self.object.pk])


class LocationCreateView(LocationFormMixin, CreateView):
    success_message = "Location %(name)s was added."


class LocationUpdateView(LocationFormMixin, UpdateView):
    context_object_name = "location"
    success_message = "Location %(name)s was updated."


class LocationDeleteView(OwnedLocationMixin, DeleteView):
    template_name = "devices/location_confirm_delete.html"
    context_object_name = "location"
    success_url = reverse_lazy("devices:location_list")

    def form_valid(self, form):
        # on_delete=RESTRICT membuat Django menolak penghapusan lokasi yang
        # masih dipakai device (RestrictedError; ProtectedError untuk PROTECT).
        try:
            response = super().form_valid(form)
        except (ProtectedError, RestrictedError):
            count = self.object.devices.count()
            messages.error(
                self.request,
                f"{self.object.name} is still used by {count} device(s). "
                "Move those devices to another location first.",
            )
            return redirect("devices:location_detail", pk=self.object.pk)
        messages.success(self.request, "Location was deleted.")
        return response


class DeviceTokenRegenerateView(OwnedDeviceMixin, View):
    """Membuat access token baru untuk device. Hanya menerima POST.

    Dipakai bila token bocor. Device fisik harus diisi token baru, karena
    token lama langsung ditolak oleh API.
    """

    http_method_names = ["post"]

    def post(self, request, pk):
        device = get_object_or_404(self.get_queryset(), pk=pk)
        access.require(request.user, device, access.EDITOR)
        device.regenerate_token()
        messages.success(
            request,
            f"New access token created for {device.name}. Update the device with the new token.",
        )
        return redirect(reverse("devices:detail", args=[device.pk]) + "#connect")
