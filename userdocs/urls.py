"""URL dokumentasi pengguna. Semua diawali /docs/ (lihat config/urls.py)."""
from django.urls import path

from . import views

app_name = "userdocs"

urlpatterns = [
    path("", views.index, name="index"),
    path("search/", views.search_view, name="search"),
    path("<slug:slug>/", views.page, name="page"),
]
