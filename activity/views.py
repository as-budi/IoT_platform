"""Halaman log aktivitas: /activity/ (tahap 18)."""
from datetime import datetime, time, timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.utils import timezone
from django.views.generic import ListView

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


class ActivityListView(LoginRequiredMixin, ListView):
    template_name = "activity/activity_list.html"
    context_object_name = "events"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        events = visible_events(self.request.user)
        self.form = ActivityFilterForm(self.request.GET or None, user=self.request.user)
        if not self.form.is_valid():
            return events
        data = self.form.cleaned_data
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
