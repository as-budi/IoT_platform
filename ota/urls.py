"""URL halaman OTA. Semua diawali /firmware/ (lihat config/urls.py).

Endpoint untuk DEVICE (unduhan dan laporan) ada di api/urls.py, di bawah /api/v1/.
"""
from django.urls import path

from . import views

app_name = "ota"

urlpatterns = [
    path("", views.PackageListView.as_view(), name="package_list"),
    path("upload/", views.PackageUploadView.as_view(), name="package_upload"),
    path("<int:pk>/", views.PackageDetailView.as_view(), name="package_detail"),
    path("<int:pk>/delete/", views.PackageDeleteView.as_view(), name="package_delete"),
    path("devices/<int:device_pk>/update/", views.StartUpdateView.as_view(), name="start"),
    path("updates/<int:pk>/", views.UpdateDetailView.as_view(), name="update_detail"),
    path("updates/<int:pk>/cancel/", views.CancelUpdateView.as_view(), name="cancel"),
]
