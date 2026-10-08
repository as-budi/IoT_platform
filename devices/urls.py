"""URL milik app devices. Semua diawali /devices/ (lihat config/urls.py)."""
from django.urls import path

from . import views

app_name = "devices"

urlpatterns = [
    # Device
    path("", views.DeviceListView.as_view(), name="list"),
    path("add/", views.DeviceCreateView.as_view(), name="add"),
    path("<int:pk>/", views.DeviceDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.DeviceUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.DeviceDeleteView.as_view(), name="delete"),
    # Variabel milik sebuah device. URL-nya bertingkat:
    #   /devices/<device_pk>/variables/<pk>/
    # sehingga dari alamatnya saja sudah jelas variabel ini milik device mana.
    path("<int:device_pk>/variables/add/", views.VariableCreateView.as_view(), name="variable_add"),
    path("<int:device_pk>/variables/<int:pk>/", views.VariableDetailView.as_view(), name="variable_detail"),
    path("<int:device_pk>/variables/<int:pk>/edit/", views.VariableUpdateView.as_view(), name="variable_edit"),
    path("<int:device_pk>/variables/<int:pk>/delete/", views.VariableDeleteView.as_view(), name="variable_delete"),
    # Kontrol (hanya POST):
    #   pola 1 — ubah desired state sebuah aktuator
    path("<int:device_pk>/variables/<int:pk>/set/", views.SetDesiredView.as_view(), name="variable_set"),
    #   pola 2 — kirim command (aksi sekali jalan) ke device
    path("<int:device_pk>/commands/add/", views.CommandCreateView.as_view(), name="command_add"),
    # Lokasi
    path("locations/", views.LocationListView.as_view(), name="location_list"),
    path("locations/add/", views.LocationCreateView.as_view(), name="location_add"),
    path("locations/<int:pk>/", views.LocationDetailView.as_view(), name="location_detail"),
    path("locations/<int:pk>/edit/", views.LocationUpdateView.as_view(), name="location_edit"),
    path("locations/<int:pk>/delete/", views.LocationDeleteView.as_view(), name="location_delete"),
]
