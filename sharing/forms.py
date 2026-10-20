from django import forms
from django.contrib.auth import get_user_model

from accounts.forms import BootstrapFormMixin

from .models import ROLE_HELP, DeviceShare, Role, ShareGroup, ShareGroupMember


def find_user(username):
    """Pengguna aktif dengan username ini (tanpa membedakan huruf besar/kecil), atau None."""
    username = (username or "").strip()
    if not username:
        return None
    return get_user_model().objects.filter(username__iexact=username, is_active=True).first()


def groups_for(user):
    """Grup yang boleh DIPAKAI `user` untuk berbagi: grup yang ia ikuti (Admin: semua)."""
    groups = ShareGroup.objects.select_related("owner")
    return groups if user.is_admin else groups.filter(members__user=user)


class ShareForm(BootstrapFormMixin, forms.Form):
    """Bagikan satu device ke satu pengguna ATAU satu grup.

    Bila device sudah dibagikan ke sasaran yang sama, perannya diganti (tidak
    membuat baris kedua).
    """

    username = forms.CharField(label="User", required=False, max_length=150,
                               widget=forms.TextInput(attrs={"placeholder": "username", "autocomplete": "off"}))
    group = forms.ModelChoiceField(label="or group", required=False, queryset=None, empty_label="— No group —")
    role = forms.ChoiceField(label="Role", choices=Role.choices, initial=Role.VIEWER,
                             help_text=" ".join(f"{Role(r).label}: {text}" for r, text in ROLE_HELP.items()))

    def __init__(self, *args, device, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.device = device
        self.fields["group"].queryset = groups_for(user)
        self.fields["group"].label_from_instance = lambda g: f"{g.name} ({g.owner.username})"

    def clean(self):
        cleaned = super().clean()
        username, group = cleaned.get("username", "").strip(), cleaned.get("group")
        if bool(username) == bool(group):
            raise forms.ValidationError("Type a username or choose a group (one of them).")
        if username:
            target = find_user(username)
            if target is None:
                self.add_error("username", f'No active user called "{username}".')
            elif target.pk == self.device.owner_id:
                self.add_error("username", f"{target.username} already owns this device.")
            elif target.is_admin:
                self.add_error("username", f"{target.username} is an Admin and already has access to every device.")
            cleaned["user"] = target
        return cleaned

    def save(self, created_by):
        """Buat share baru atau ganti perannya. Mengembalikan (share, created)."""
        target = {"user": self.cleaned_data["user"]} if self.cleaned_data.get("user") else {
            "group": self.cleaned_data["group"]}
        share = DeviceShare.objects.filter(device=self.device, **target).first()
        if share is None:
            share = DeviceShare.objects.create(device=self.device, role=self.cleaned_data["role"],
                                               created_by=created_by, **target)
            return share, True
        if share.role != self.cleaned_data["role"]:
            share.role = self.cleaned_data["role"]
            share.save(update_fields=["role"])
        return share, False


class ShareRoleForm(forms.Form):
    role = forms.ChoiceField(choices=Role.choices)


class GroupForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ShareGroup
        fields = ("name", "description")

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        same = ShareGroup.objects.filter(owner=self.owner, name__iexact=name).exclude(pk=self.instance.pk)
        if same.exists():
            raise forms.ValidationError("You already have a group with this name.")
        return name


class MemberForm(BootstrapFormMixin, forms.Form):
    username = forms.CharField(label="Username", max_length=150,
                               widget=forms.TextInput(attrs={"placeholder": "username", "autocomplete": "off"}))

    def __init__(self, *args, group, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        user = find_user(username)
        if user is None:
            raise forms.ValidationError(f'No active user called "{username}".')
        if ShareGroupMember.objects.filter(group=self.group, user=user).exists():
            raise forms.ValidationError(f"{user.username} is already a member.")
        self.cleaned_data["user"] = user
        return username
