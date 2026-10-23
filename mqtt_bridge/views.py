"""
Akun MQTT sandbox milik pengguna yang login (tahap 23), dari halaman My profile.

    POST /accounts/sandbox/enable/    buat akun (password acak)
    POST /accounts/sandbox/reset/     password baru (yang lama langsung tidak berlaku, ±2 menit)
    POST /accounts/sandbox/disable/   hapus akun

Akun baru/berubah sampai ke broker paling lambat ±2 menit kemudian (timer mqtt_accounts).
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from .models import SandboxAccount


def _record(verb, message, user):
    from activity.log import Category, record   # tahap 18

    record(verb, message, category=Category.ACCOUNT, owner=user)


def _check_enabled():
    if not settings.MQTT_SANDBOX_ENABLED:
        raise Http404("The MQTT sandbox is not available on this server.")


@login_required
@require_POST
def enable(request):
    _check_enabled()
    account, created = SandboxAccount.objects.get_or_create(user=request.user)
    if created:
        _record("sandbox.enabled", f"MQTT sandbox account {account.username} created", request.user)
        messages.success(request, "Your MQTT sandbox account was created. It works on the broker within about "
                                  "2 minutes.")
    return redirect("accounts:profile")


@login_required
@require_POST
def reset(request):
    _check_enabled()
    account = SandboxAccount.objects.filter(user=request.user).first()
    if account:
        account.reset_password()
        _record("sandbox.reset", f"MQTT sandbox password of {account.username} reset", request.user)
        messages.success(request, "New sandbox password created. Clients using the old one are refused "
                                  "within about 2 minutes.")
    return redirect("accounts:profile")


@login_required
@require_POST
def disable(request):
    _check_enabled()
    deleted, _ = SandboxAccount.objects.filter(user=request.user).delete()
    if deleted:
        _record("sandbox.disabled", "MQTT sandbox account removed", request.user)
        messages.info(request, "Your MQTT sandbox account was removed.")
    return redirect("accounts:profile")


def sandbox_context(user):
    """Data kartu "MQTT sandbox" di halaman profil."""
    from devices.views import mqtt_info
    from devices.models import Device

    if not settings.MQTT_SANDBOX_ENABLED:
        return {"sandbox_available": False}
    return {
        "sandbox_available": True,
        "sandbox": SandboxAccount.objects.filter(user=user).first(),
        "sandbox_broker": mqtt_info(Device(access_token="")),
        "sandbox_shared_root": "sandbox/public",
    }
