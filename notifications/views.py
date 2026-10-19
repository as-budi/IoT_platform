"""Halaman menyambungkan / memutus Telegram dan pesan uji (tahap 17)."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.views import View

from . import link, telegram
from .models import TelegramLink


class TelegramConnectView(LoginRequiredMixin, View):
    """GET: petunjuk + tautan t.me. POST action=check: cari pesan /start dari pengguna ini."""

    template_name = "notifications/telegram_connect.html"

    def get(self, request):
        if not telegram.enabled():
            messages.error(request, "Telegram is not configured on this server.")
            return redirect("accounts:profile")
        try:
            url = link.deep_link(link.new_code(request.user))
        except telegram.TelegramError as exc:
            messages.error(request, str(exc))
            return redirect("accounts:profile")
        return render(request, self.template_name, {"telegram_url": url, "minutes": link.CODE_MINUTES})

    def post(self, request):
        try:
            link.check_updates()
        except telegram.TelegramError as exc:
            messages.error(request, str(exc))
            return redirect("accounts:profile")
        if TelegramLink.objects.filter(user=request.user).exists():
            messages.success(request, "Telegram connected. Rule notifications can now be sent there.")
            return redirect("accounts:profile")
        messages.warning(request, "No Start message from you yet. Open the link, press START in Telegram, "
                                  "then press the button again.")
        return redirect("notifications:telegram_connect")


class TelegramTestView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        chat = TelegramLink.objects.filter(user=request.user).first()
        if chat is None:
            messages.error(request, "Connect Telegram first.")
        else:
            try:
                telegram.send_message(chat.chat_id, f"Test message from the IoT platform for {request.user.username}.")
                messages.success(request, "Test message sent. Check Telegram.")
            except telegram.TelegramError as exc:
                messages.error(request, str(exc))
        return redirect("accounts:profile")


class TelegramDisconnectView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        TelegramLink.objects.filter(user=request.user).delete()
        messages.success(request, "Telegram disconnected.")
        return redirect("accounts:profile")
