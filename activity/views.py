"""Halaman log aktivitas: /activity/ (tahap 18)."""
from datetime import datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView, View

from .forms import ActivityFilterForm
from .log import visible_events
from .models import ActivityEvent

PAGE_SIZE = 50


def search(events, text):
    """Setiap kata harus muncul di salah satu kolom teks (AND antar-kata)."""
    for word in text.split()[:8]:
        events = events.filter(
            Q(message__icontains=word) | Q(device_name__icontains=word) | Q(rule_name__icontains=word)
            | Q(actor_name__icontains=word) | Q(verb__icontains=word)
        )
    return events


def filtered_events(request):
    """Event yang boleh dilihat pengguna, disaring sesuai query string. -> (events, form).

    Dipakai halaman /activity/ dan unduhan Excel, supaya keduanya selalu sama."""
    events = visible_events(request.user)
    form = ActivityFilterForm(request.GET or None, user=request.user)
    if not form.is_valid():
        return events, form
    data = form.cleaned_data
    if data.get("q"):
        events = search(events, data["q"])
    if data.get("category"):
        events = events.filter(category=data["category"])
    if data.get("level") == "WARNING":
        events = events.filter(level__in=[ActivityEvent.Level.WARNING, ActivityEvent.Level.ERROR])
    elif data.get("level") == "ERROR":
        events = events.filter(level=ActivityEvent.Level.ERROR)
    if data.get("device"):
        events = events.filter(device=data["device"])
    if data.get("owner"):
        events = events.filter(owner=data["owner"])
    if data.get("start"):
        events = events.filter(at__gte=timezone.make_aware(datetime.combine(data["start"], time.min)))
    if data.get("end"):
        next_day = datetime.combine(data["end"] + timedelta(days=1), time.min)
        events = events.filter(at__lt=timezone.make_aware(next_day))
    return events, form


class ActivityListView(LoginRequiredMixin, ListView):
    template_name = "activity/activity_list.html"
    context_object_name = "events"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        events, self.form = filtered_events(self.request)
        return events

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = self.form
        # Query string tanpa nomor halaman, untuk tautan halaman berikutnya.
        query = self.request.GET.copy()
        query.pop("page", None)
        context["query"] = query.urlencode()
        context["filtered"] = any(v for k, v in self.request.GET.items() if k != "page")
        return context


class ActivityExportView(LoginRequiredMixin, View):
    """GET /activity/export.xlsx?<filter halaman> — log aktivitas sebagai file Excel (activity/export.py)."""

    http_method_names = ["get"]

    def get(self, request):
        from . import export

        events, _form = filtered_events(request)
        total = events.count()
        if total > export.MAX_ROWS:
            messages.error(request, f"{total:,} events are too many for one file (limit {export.MAX_ROWS:,}). "
                                    "Narrow the filters, e.g. a shorter date range.")
            query = request.GET.urlencode()
            return redirect(reverse("activity:list") + (f"?{query}" if query else ""))
        return FileResponse(
            export.xlsx_file(events, include_owner=request.user.is_admin), as_attachment=True,
            filename=export.filename(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
