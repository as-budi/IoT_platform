"""
Halaman OTA (paket firmware, detail pembaruan) dan endpoint unduhan untuk device.

Kepemilikan:
- Paket firmware milik pengunggahnya; Admin melihat semua paket.
- Pembaruan mengikuti device: siapa yang boleh melihat device boleh memperbaruinya,
  memakai paket yang boleh ia lihat.
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_GET
from django.views.generic import CreateView, DeleteView, DetailView, ListView, View

from devices.views import data_version, visible_devices
from sharing import access
from realtime.routing import device_ws_path

from . import services
from .forms import FirmwareUploadForm, StartUpdateForm
from .models import FirmwarePackage, OtaStatus, OtaUpdate

RECENT_UPDATES = 5


def visible_packages(user):
    packages = FirmwarePackage.objects.select_related("uploaded_by")
    return packages if user.is_admin else packages.filter(uploaded_by=user)


def visible_updates(user):
    return OtaUpdate.objects.select_related("device", "package", "created_by", "command").filter(
        device__in=visible_devices(user)
    )


# --- Paket firmware ------------------------------------------------------------------

class PackageListView(LoginRequiredMixin, ListView):
    template_name = "ota/package_list.html"
    context_object_name = "packages"

    def get_queryset(self):
        return visible_packages(self.request.user).annotate(
            update_count=Count("updates"),
            updated_count=Count("updates", filter=Q(updates__status=OtaStatus.UPDATED)),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        services.expire_due()
        context["recent_updates"] = visible_updates(self.request.user)[:10]
        return context


class PackageUploadView(LoginRequiredMixin, CreateView):
    template_name = "ota/package_form.html"
    form_class = FirmwareUploadForm

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "user": self.request.user}

    def form_valid(self, form):
        package = form.save()
        messages.success(self.request, f"Firmware {package.title} {package.version} uploaded.")
        return redirect("ota:package_detail", pk=package.pk)


class PackageDetailView(LoginRequiredMixin, DetailView):
    template_name = "ota/package_detail.html"
    context_object_name = "package"

    def get_queryset(self):
        return visible_packages(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        services.expire_due()
        context["updates"] = visible_updates(self.request.user).filter(package=self.object)[:50]
        return context


class PackageDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "ota/package_confirm_delete.html"
    context_object_name = "package"
    success_url = reverse_lazy("ota:package_list")

    def get_queryset(self):
        return visible_packages(self.request.user)

    def form_valid(self, form):
        package = self.get_object()
        if package.updates.exists():
            messages.error(self.request, "This firmware was used by updates and is kept for their history.")
            return redirect("ota:package_detail", pk=package.pk)
        package.file.delete(save=False)  # hapus juga file .bin-nya dari disk
        messages.success(self.request, f"Firmware {package.title} {package.version} deleted.")
        return super().form_valid(form)


# --- Pembaruan ---------------------------------------------------------------------------

def device_firmware_context(request, device):
    """Data kartu "Firmware" di halaman device (devices/views.DeviceDetailView)."""
    services.expire_due(device)
    updates = list(device.ota_updates.select_related("package", "created_by").prefetch_related("events")[:RECENT_UPDATES])
    active = next((u for u in updates if not u.is_final), None)
    packages = services.compatible_packages(visible_packages(request.user), device)
    base_url = services.base_url_for(request)
    return {
        "ota_active": active,
        "ota_recent": updates,
        "ota_form": StartUpdateForm(packages=packages),
        "ota_has_packages": packages.exists(),
        "ota_base_url": base_url,
        "ota_reach": services.reach(base_url),
    }


class StartUpdateView(LoginRequiredMixin, View):
    """POST /firmware/devices/<device_pk>/update/ — mulai memperbarui satu device."""

    http_method_names = ["post"]

    def post(self, request, device_pk):
        device = get_object_or_404(visible_devices(request.user), pk=device_pk)
        access.require(request.user, device, access.EDITOR)   # firmware = mengubah device (tahap 19)
        packages = services.compatible_packages(visible_packages(request.user), device)
        form = StartUpdateForm(request.POST, packages=packages)
        if not form.is_valid():
            messages.error(request, "Choose a firmware version for this device's board.")
        else:
            try:
                update = services.start_update(
                    device, form.cleaned_data["package"], request.user, services.base_url_for(request)
                )
                messages.success(
                    request,
                    f"Update to {update.package.version} queued. The device receives it as the "
                    f"command \"ota\" the next time it checks for commands.",
                )
            except services.OtaError as exc:
                messages.error(request, str(exc))
        return redirect(reverse("devices:detail", args=[device.pk]) + "#firmware")


class UpdateDetailView(LoginRequiredMixin, DetailView):
    template_name = "ota/update_detail.html"
    context_object_name = "update"

    def get_queryset(self):
        return visible_updates(self.request.user).prefetch_related("events")

    def get_object(self, queryset=None):
        update = super().get_object(queryset)
        services.expire_due(update.device)
        update.refresh_from_db()
        return update

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        device = self.object.device
        # Pembaruan langsung memakai saluran device (kabar "ota" dikirim ke sana).
        context.update(
            ws_path=device_ws_path(device.pk),
            updates_url=reverse("devices:updates", args=[device.pk]),
            data_version=data_version(device),
        )
        return context


class CancelUpdateView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        update = get_object_or_404(visible_updates(request.user), pk=pk)
        access.require(request.user, update.device, access.EDITOR)
        try:
            services.cancel(update, request.user)
            messages.success(request, "Update canceled.")
        except services.OtaError as exc:
            messages.error(request, str(exc))
        return redirect(reverse("devices:detail", args=[update.device_id]) + "#firmware")


# --- Unduhan untuk device ----------------------------------------------------------------

@require_GET
def download(request, token):
    """GET /api/v1/ota/<token>.bin — device mengunduh file firmware.

    Tanpa login: device dikenali dari token unduhan (sekali pakai, kedaluwarsa).
    Header yang dikirim:
      Content-Length  ukuran file; HTTPUpdate memastikan partisi cukup
      x-MD5           checksum; HTTPUpdate menolak image yang tidak cocok
    """
    update, rejected = services.claim_download(token, request.META.get("REMOTE_ADDR"))
    if rejected:
        status, message = rejected
        return JsonResponse({"error": message}, status=status)
    package = update.package
    response = FileResponse(
        package.file.open("rb"),
        as_attachment=True,
        filename=f"{package.board}-{package.version}.bin",
        content_type="application/octet-stream",
    )
    response["Content-Length"] = package.size
    response["x-MD5"] = package.md5
    # Jangan disimpan proxy/browser: link ini sekali pakai.
    response["Cache-Control"] = "no-store"
    return response
