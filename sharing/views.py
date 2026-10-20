"""
Halaman berbagi device (tahap 19).

    Device detail → kartu "Sharing"   (pemilik / Admin)  tambah, ubah peran, hapus akses
    /sharing/groups/                  grup yang saya ikuti; buat grup, kelola anggota

Semua aksi hanya POST. Device yang tidak terlihat = 404; terlihat tetapi bukan
pemilik = 403 (lihat sharing/access.require).
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView, View

from . import access
from .forms import GroupForm, MemberForm, ShareForm, ShareRoleForm
from .models import DeviceShare, Role, ShareGroup, ShareGroupMember


def sharing_url(device_id):
    return reverse("devices:detail", args=[device_id]) + "#sharing"


def form_errors(request, form):
    for field, errors in form.errors.items():
        label = form[field].label if field in form.fields else ""
        for error in errors:
            messages.error(request, f"{label}: {error}" if label else error)


# --- Berbagi satu device ------------------------------------------------------------------

class ShareCreateView(LoginRequiredMixin, View):
    """POST /sharing/devices/<device_pk>/add/ — bagikan ke pengguna atau grup."""

    http_method_names = ["post"]

    def post(self, request, device_pk):
        device = get_object_or_404(access.devices_for(request.user), pk=device_pk)
        access.require(request.user, device, access.OWNER)
        form = ShareForm(request.POST, device=device, user=request.user)
        if form.is_valid():
            share, created = form.save(created_by=request.user)
            verb = "Shared" if created else "Updated access for"
            messages.success(request, f"{verb} {device.name} with {share.target_name} as {share.get_role_display()}.")
        else:
            form_errors(request, form)
        return redirect(sharing_url(device.pk))


class ShareRoleView(LoginRequiredMixin, View):
    """POST /sharing/shares/<pk>/role/ — ganti peran (pemilik device)."""

    http_method_names = ["post"]

    def post(self, request, pk):
        share = get_object_or_404(DeviceShare.objects.select_related("device", "user", "group"), pk=pk)
        access.require(request.user, share.device, access.OWNER)
        form = ShareRoleForm(request.POST)
        if form.is_valid() and form.cleaned_data["role"] != share.role:
            share.role = form.cleaned_data["role"]
            share.save(update_fields=["role"])
            messages.success(request, f"{share.target_name} is now {share.get_role_display()} of {share.device.name}.")
        return redirect(sharing_url(share.device_id))


class ShareDeleteView(LoginRequiredMixin, View):
    """POST /sharing/shares/<pk>/delete/

    Pemilik device / Admin: mencabut akses siapa saja.
    Penerima share langsung: "Leave" — melepas aksesnya sendiri.
    """

    http_method_names = ["post"]

    def post(self, request, pk):
        share = get_object_or_404(DeviceShare.objects.select_related("device", "user", "group"), pk=pk)
        device = share.device
        if share.user_id == request.user.pk and not access.can(request.user, device, access.OWNER):
            share.delete()
            messages.success(request, f"You no longer have access to {device.name}.")
            return redirect("devices:list")
        access.require(request.user, device, access.OWNER)
        share.delete()
        messages.success(request, f"{share.target_name} no longer has access to {device.name}.")
        return redirect(sharing_url(device.pk))


def device_sharing_context(request, device, role):
    """Data kartu "Sharing" di halaman device (dipanggil DeviceDetailView)."""
    context = {"my_role": role, "my_role_label": access.LABEL.get(role, "")}
    if role == access.OWNER:
        context["shares"] = device.shares.select_related("user", "group__owner", "created_by").annotate(
            member_count=Count("group__members"))
        context["share_form"] = ShareForm(device=device, user=request.user)
        context["roles"] = Role.choices
    else:
        # Penerima: dari mana aksesnya (langsung dan/atau lewat grup).
        context["my_shares"] = access.shares_of(request.user).filter(device=device).select_related("group", "created_by")
    return context


# --- Grup -----------------------------------------------------------------------------------

def visible_groups(user):
    groups = ShareGroup.objects.select_related("owner").annotate(
        member_count=Count("members", distinct=True), device_count=Count("device_shares", distinct=True))
    return groups if user.is_admin else groups.filter(members__user=user)


def can_manage(user, group):
    return user.is_admin or group.owner_id == user.pk


class GroupListView(LoginRequiredMixin, ListView):
    template_name = "sharing/group_list.html"
    context_object_name = "groups"

    def get_queryset(self):
        return visible_groups(self.request.user)


class GroupDetailView(LoginRequiredMixin, DetailView):
    template_name = "sharing/group_detail.html"
    context_object_name = "group"

    def get_queryset(self):
        return visible_groups(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        group = self.object
        context["members"] = group.members.select_related("user")
        context["can_manage"] = can_manage(self.request.user, group)
        context["member_form"] = MemberForm(group=group)
        context["shares"] = group.device_shares.select_related("device__owner")
        context["is_member"] = group.members.filter(user=self.request.user).exists()
        return context


class GroupFormMixin(LoginRequiredMixin):
    form_class = GroupForm
    template_name = "sharing/group_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["owner"] = self.object.owner if getattr(self, "object", None) else self.request.user
        return kwargs

    def get_success_url(self):
        return reverse("sharing:group_detail", args=[self.object.pk])


class GroupCreateView(GroupFormMixin, CreateView):
    def form_valid(self, form):
        form.instance.owner = self.request.user
        response = super().form_valid(form)
        # Pembuat grup otomatis anggota (dan tidak bisa dikeluarkan).
        ShareGroupMember.objects.create(group=self.object, user=self.request.user)
        messages.success(self.request, f"Group {self.object.name} created. Add members below.")
        return response


class ManagedGroupMixin(LoginRequiredMixin):
    """Hanya pemilik grup (atau Admin) yang boleh mengubah / menghapus grup."""

    def get_queryset(self):
        groups = ShareGroup.objects.select_related("owner")
        return groups if self.request.user.is_admin else groups.filter(owner=self.request.user)


class GroupUpdateView(ManagedGroupMixin, GroupFormMixin, UpdateView):
    context_object_name = "group"

    def form_valid(self, form):
        messages.success(self.request, "Group saved.")
        return super().form_valid(form)


class GroupDeleteView(ManagedGroupMixin, DeleteView):
    template_name = "sharing/group_confirm_delete.html"
    context_object_name = "group"
    success_url = reverse_lazy("sharing:group_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["share_count"] = self.object.device_shares.count()
        return context

    def form_valid(self, form):
        messages.success(self.request, f"Group {self.object.name} deleted; its members lost the access it gave.")
        return super().form_valid(form)


class MemberAddView(ManagedGroupMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        group = get_object_or_404(self.get_queryset(), pk=pk)
        form = MemberForm(request.POST, group=group)
        if form.is_valid():
            member = ShareGroupMember.objects.create(group=group, user=form.cleaned_data["user"])
            messages.success(request, f"{member.user.username} added to {group.name}.")
        else:
            form_errors(request, form)
        return redirect("sharing:group_detail", pk=group.pk)


class MemberRemoveView(LoginRequiredMixin, View):
    """Pemilik grup mengeluarkan anggota, atau anggota keluar sendiri ("Leave")."""

    http_method_names = ["post"]

    def post(self, request, pk, member_pk):
        member = get_object_or_404(ShareGroupMember.objects.select_related("group", "user"), pk=member_pk, group_id=pk)
        group = member.group
        leaving = member.user_id == request.user.pk
        if not leaving and not can_manage(request.user, group):
            raise PermissionDenied("Only the group owner can remove members.")
        if member.user_id == group.owner_id:
            messages.error(request, "The group owner cannot leave the group. Delete the group instead.")
            return redirect("sharing:group_detail", pk=group.pk)
        member.delete()
        if leaving:
            messages.success(request, f"You left {group.name}.")
            return redirect("sharing:group_list")
        messages.success(request, f"{member.user.username} removed from {group.name}.")
        return redirect("sharing:group_detail", pk=group.pk)
