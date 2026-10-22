"""
Halaman milik app oauth (tahap 22).

    /o/authorize/                 halaman persetujuan ("Allow MQTT Client to ...?")
    /accounts/apps/               Profile -> Connected apps: aplikasi yang diberi izin
    /accounts/apps/<id>/revoke/   cabut izin satu aplikasi (POST)

Alamat OAuth/OIDC lain (/o/token/, /o/userinfo/, /o/.well-known/...) memakai view
bawaan django-oauth-toolkit apa adanya (lihat oauth/urls.py).
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import ListView
from oauth2_provider.exceptions import OAuthToolkitError
from oauth2_provider.models import get_application_model
from oauth2_provider.views import AuthorizationView

from .models import Consent
from .services import revoke_consent

# Scope yang perlu ditandai mencolok di halaman persetujuan.
SENSITIVE_SCOPES = {"devices:read"}


def _record(verb, message, user):
    from activity.log import Category, record   # tahap 18

    record(verb, message, category=Category.ACCOUNT, owner=user)


class PlatformAuthorizationView(AuthorizationView):
    """GET/POST /o/authorize/ — view bawaan django-oauth-toolkit, ditambah:

    - template sendiri (Bootstrap, seperti halaman lain);
    - persetujuan DIINGAT (model Consent): bila semua scope yang diminta sudah pernah
      disetujui, pengguna langsung dikembalikan ke aplikasi tanpa ditanya lagi;
    - persetujuan dicatat di log aktivitas.

    Pengguna yang belum login diarahkan ke halaman login dulu (bawaan view ini),
    lalu kembali ke sini lewat parameter `next`.
    """

    template_name = "oauth/authorize.html"

    def render_to_response(self, context, **response_kwargs):
        # Dipanggil HANYA bila halaman persetujuan akan ditampilkan (sesudah semua
        # pemeriksaan permintaan oleh view bawaan). Di sinilah persetujuan lama dipakai.
        application = context.get("application")
        scopes = context.get("scopes") or []
        if (
            self.request.method == "GET"
            and application is not None
            and not context.get("error")
            and "consent" not in self.request.GET.get("prompt", "").split()
        ):
            consent = Consent.objects.filter(user=self.request.user, application=application).first()
            if consent and consent.covers(scopes):
                try:
                    _, credentials = self.validate_authorization_request(self.request)
                    uri, _headers, _body, _status = self.create_authorization_response(
                        request=self.request, scopes=" ".join(scopes), credentials=credentials, allow=True
                    )
                except OAuthToolkitError as error:
                    return self.error_response(error, application)
                return self.redirect(uri, application)

        context["scope_items"] = [
            {"name": name, "text": settings.OAUTH_SCOPES.get(name, name), "sensitive": name in SENSITIVE_SCOPES}
            for name in scopes
        ]
        return super().render_to_response(context, **response_kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        if form.cleaned_data.get("allow"):
            application = get_application_model().objects.get(client_id=form.cleaned_data["client_id"])
            scopes = form.cleaned_data.get("scope", "").split()
            consent, created = Consent.objects.get_or_create(
                user=self.request.user, application=application, defaults={"scope": " ".join(scopes)}
            )
            if not created and not consent.covers(scopes):
                consent.scope = " ".join(sorted(set(consent.scopes) | set(scopes)))
                consent.save(update_fields=["scope"])
            _record("oauth.authorized", f"Allowed {application.name} to access the account ({' '.join(scopes)})",
                    self.request.user)
        return response


class ConnectedAppsView(LoginRequiredMixin, ListView):
    """GET /accounts/apps/ — aplikasi luar yang pernah diberi izin oleh pengguna ini."""

    template_name = "oauth/connected_apps.html"
    context_object_name = "consents"

    def get_queryset(self):
        return Consent.objects.filter(user=self.request.user).select_related("application")


class RevokeAppView(LoginRequiredMixin, View):
    """POST /accounts/apps/<id>/revoke/ — cabut izin satu aplikasi.

    `id` adalah id baris Consent milik pengguna yang login; milik orang lain -> 404.
    """

    def post(self, request, pk):
        consent = get_object_or_404(Consent.objects.select_related("application"), pk=pk, user=request.user)
        name = consent.application.name
        revoke_consent(request.user, consent.application)
        _record("oauth.revoked", f"Revoked access of {name}", request.user)
        messages.success(request, f"{name} can no longer access your account.")
        return redirect("oauth:connected_apps")
