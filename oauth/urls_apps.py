"""Profile -> Connected apps (tahap 22). Diawali /accounts/apps/ (lihat config/urls.py)."""
from django.urls import path

from . import views

app_name = "oauth"

urlpatterns = [
    path("", views.ConnectedAppsView.as_view(), name="connected_apps"),
    path("<int:pk>/revoke/", views.RevokeAppView.as_view(), name="revoke"),
]
