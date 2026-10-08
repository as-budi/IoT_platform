from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core.paginator import Paginator
from django.db.models import OuterRef, Subquery
from django.urls import reverse, reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from .forms import DeviceForm
from .models import Device, Telemetry

HISTORY_PAGE_SIZE = 25


class OwnedDeviceMixin(LoginRequiredMixin):
    """Inti aturan kepemilikan: membatasi device yang boleh diakses.

    Semua view device mengambil datanya lewat get_queryset() ini, sehingga:
    - Admin melihat semua device.
    - Pengguna biasa hanya melihat device miliknya. Bila ia menebak URL
      device orang lain, device itu "tidak ada" di queryset -> 404.
    """

    def get_queryset(self):
        # select_related("owner") mengambil data pemilik dalam query yang sama
        # (JOIN), supaya tidak ada query tambahan per baris di template.
        queryset = Device.objects.select_related("owner")
        if self.request.user.is_admin:
            return queryset
        return queryset.filter(owner=self.request.user)


class DeviceListView(OwnedDeviceMixin, ListView):
    template_name = "devices/device_list.html"
    context_object_name = "devices"

    def get_queryset(self):
        # Untuk tiap device kita ingin nilai terbarunya tanpa query per baris.
        # Subquery = query kecil yang ditanam di dalam query utama;
        # OuterRef("pk") merujuk ke device pada baris yang sedang diproses.
        newest = Telemetry.objects.filter(device=OuterRef("pk")).order_by("-ts")
        return (
            super()
            .get_queryset()
            .annotate(
                last_value=Subquery(newest.values("value")[:1]),
                last_seen=Subquery(newest.values("ts")[:1]),
            )
        )


class DeviceDetailView(OwnedDeviceMixin, DetailView):
    template_name = "devices/device_detail.html"
    context_object_name = "device"

    def get_context_data(self, **kwargs):
        """Menyiapkan data tambahan yang akan dipakai template."""
        context = super().get_context_data(**kwargs)
        device = self.object

        # 1) Card: nilai terkini (satu device = satu variabel).
        context["latest"] = device.latest

        # 2) Tabel history. Paginator memotong data menjadi halaman-halaman
        # (?page=2, dst.) supaya ribuan baris tidak dimuat sekaligus.
        paginator = Paginator(device.telemetry.all(), HISTORY_PAGE_SIZE)
        context["page_obj"] = paginator.get_page(self.request.GET.get("page"))
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
