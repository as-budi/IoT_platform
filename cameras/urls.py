"""URL halaman kamera, diawali /cameras/ (config/urls.py). Unggahan device ada di api/urls.py."""
from django.urls import path

from . import views

app_name = "cameras"

urlpatterns = [
    path("snapshots/<int:pk>.jpg", views.image, name="image"),
    path("snapshots/<int:pk>/delete/", views.delete, name="delete"),
    path("devices/<int:pk>/", views.gallery, name="gallery"),
    path("devices/<int:pk>/capture/", views.capture, name="capture"),
    path("devices/<int:pk>/settings/", views.camera_settings, name="settings"),
]
