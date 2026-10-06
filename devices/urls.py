"""URL milik app devices. Semua diawali /devices/ (lihat config/urls.py)."""
from django.urls import path

from . import views

app_name = "devices"

urlpatterns = [
    path("", views.DeviceListView.as_view(), name="list"),
    path("add/", views.DeviceCreateView.as_view(), name="add"),
    path("<int:pk>/", views.DeviceDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.DeviceUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.DeviceDeleteView.as_view(), name="delete"),
]
