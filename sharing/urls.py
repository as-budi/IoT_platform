"""URL berbagi device dan grup (tahap 19). Semua diawali /sharing/ (lihat config/urls.py)."""
from django.urls import path

from . import views

app_name = "sharing"

urlpatterns = [
    # Berbagi satu device (tombol di kartu "Sharing" halaman device; hanya POST).
    path("devices/<int:device_pk>/add/", views.ShareCreateView.as_view(), name="share_add"),
    path("shares/<int:pk>/role/", views.ShareRoleView.as_view(), name="share_role"),
    path("shares/<int:pk>/delete/", views.ShareDeleteView.as_view(), name="share_delete"),
    # Grup
    path("groups/", views.GroupListView.as_view(), name="group_list"),
    path("groups/add/", views.GroupCreateView.as_view(), name="group_add"),
    path("groups/<int:pk>/", views.GroupDetailView.as_view(), name="group_detail"),
    path("groups/<int:pk>/edit/", views.GroupUpdateView.as_view(), name="group_edit"),
    path("groups/<int:pk>/delete/", views.GroupDeleteView.as_view(), name="group_delete"),
    path("groups/<int:pk>/members/add/", views.MemberAddView.as_view(), name="member_add"),
    path("groups/<int:pk>/members/<int:member_pk>/remove/", views.MemberRemoveView.as_view(), name="member_remove"),
]
