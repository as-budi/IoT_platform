from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core.paginator import Paginator
from django.db.models import F, Func, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from .forms import DeviceForm, VariableForm
from .models import Device, Telemetry

HISTORY_PAGE_SIZE = 25
# Chart menampilkan 24 jam terakhir, dihitung mundur dari data terbaru
# (bukan dari "sekarang"), supaya variabel yang lama diam tetap punya grafik.
CHART_WINDOW = timedelta(hours=24)
# Batas atas jumlah titik, supaya browser tidak menerima data terlalu besar.
CHART_MAX_POINTS = 1000


# --- Aturan kepemilikan ------------------------------------------------------

def visible_devices(user):
    """Device yang boleh dilihat `user`: Admin semua, User hanya miliknya.

    Inilah SATU-SATUNYA tempat aturan kepemilikan ditulis. Semua view device
    maupun variabel mengambil datanya lewat fungsi ini.
    """
    queryset = Device.objects.select_related("owner")
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


# --- Device -----------------------------------------------------------------

class OwnedDeviceMixin(LoginRequiredMixin):
    """Membatasi view device ke device yang boleh dilihat pengguna.

    Pengguna biasa yang menebak URL device orang lain mendapat 404, karena
    device itu "tidak ada" di queryset-nya.
    """

    def get_queryset(self):
        return visible_devices(self.request.user)


class DeviceListView(OwnedDeviceMixin, ListView):
    template_name = "devices/device_list.html"
    context_object_name = "devices"

    def get_queryset(self):
        # Telemetri milik device ini = telemetri dari semua variabelnya.
        # Dua garis bawah (variable__device) berarti "ikuti relasi":
        # telemetry -> variable -> device.
        rows = Telemetry.objects.filter(variable__device=OuterRef("pk"))
        return (
            super()
            .get_queryset()
            # prefetch_related mengambil variabel SEMUA device dalam satu
            # query tambahan, bukan satu query per device di template.
            .prefetch_related("variables")
            .annotate(
                last_seen=Subquery(rows.order_by("-ts").values("ts")[:1]),
                telemetry_count=count_rows(rows),
            )
        )


class DeviceDetailView(OwnedDeviceMixin, DetailView):
    """Tampilan per device: satu card nilai terkini untuk setiap variabel."""

    template_name = "devices/device_detail.html"
    context_object_name = "device"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["variables"] = with_latest_values(self.object.variables.all())
        return context


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
    context_object_name = "device"
    success_message = "Device %(name)s was updated."


class DeviceDeleteView(OwnedDeviceMixin, SuccessMessageMixin, DeleteView):
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

    def get_device(self):
        # Disimpan di atribut supaya query device tidak diulang-ulang.
        if not hasattr(self, "_device"):
            self._device = get_object_or_404(
                visible_devices(self.request.user), pk=self.kwargs["device_pk"]
            )
        return self._device

    def get_queryset(self):
        return self.get_device().variables.all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["device"] = self.get_device()
        return context


class VariableDetailView(OwnedVariableMixin, DetailView):
    """Tampilan per variabel: nilai terkini, chart 24 jam, dan tabel history."""

    template_name = "devices/variable_detail.html"
    context_object_name = "variable"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        variable = self.object

        # 1) Card: nilai terkini.
        latest = variable.latest
        context["latest"] = latest

        # 2) Chart: deret titik {x: waktu dalam milidetik, y: nilai}, urut
        # dari lama ke baru. Format ini langsung dipahami Chart.js.
        chart_points = []
        if latest is not None:
            rows = list(
                variable.telemetry.filter(ts__gte=latest.ts - CHART_WINDOW)
                .order_by("-ts")
                .values_list("ts", "value")[:CHART_MAX_POINTS]
            )
            # Diambil dari yang terbaru (agar batas jumlah memotong data
            # lama), lalu dibalik supaya garis digambar dari kiri ke kanan.
            chart_points = [
                {"x": int(ts.timestamp() * 1000), "y": value}
                for ts, value in reversed(rows)
            ]
        context["chart_points"] = chart_points

        # 3) Tabel history. Paginator memotong data menjadi halaman-halaman
        # (?page=2, dst.) supaya ribuan baris tidak dimuat sekaligus.
        paginator = Paginator(variable.telemetry.all(), HISTORY_PAGE_SIZE)
        context["page_obj"] = paginator.get_page(self.request.GET.get("page"))
        return context


class VariableFormMixin(OwnedVariableMixin, SuccessMessageMixin):
    """Bagian yang sama antara view tambah dan ubah variabel."""

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
    template_name = "devices/variable_confirm_delete.html"
    context_object_name = "variable"
    success_message = "Variable was deleted."

    def get_success_url(self):
        return reverse("devices:detail", args=[self.get_device().pk])
