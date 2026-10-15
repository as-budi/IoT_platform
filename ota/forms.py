"""Form OTA: upload paket firmware dan memilih paket untuk sebuah device."""
import hashlib

from django import forms
from django.conf import settings

from accounts.forms import BootstrapFormMixin
from devices.models import Board

from .models import FirmwarePackage
from .services import VERSION_RE

# Byte pertama image aplikasi ESP32/ESP8266 selalu 0xE9 ("magic byte").
# Pemeriksaan murah ini menangkap kesalahan umum: mengunggah file yang salah,
# mis. .elf, .hex, atau .ino, bukan .bin hasil "Export Compiled Binary".
ESP_IMAGE_MAGIC = 0xE9


class FirmwareUploadForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = FirmwarePackage
        fields = ("title", "version", "board", "file", "notes")
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
            "file": forms.ClearableFileInput(attrs={"accept": ".bin"}),
        }
        help_texts = {
            "file": "The .bin from Arduino IDE: Sketch → Export Compiled Binary.",
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["board"].choices = [("", "— Board —")] + list(Board.choices)

    def clean_version(self):
        version = self.cleaned_data["version"].strip()
        if not VERSION_RE.match(version):
            raise forms.ValidationError("Use letters, digits and . _ + - only, e.g. 1.2.0.")
        return version

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if not upload.name.lower().endswith(".bin"):
            raise forms.ValidationError("Upload the compiled .bin file.")
        limit = settings.OTA_MAX_FIRMWARE_SIZE
        if upload.size > limit:
            raise forms.ValidationError(f"The file is larger than {limit // 1024} KB.")
        if upload.size == 0:
            raise forms.ValidationError("The file is empty.")
        # Hitung checksum sambil membaca per potongan (file besar tidak dimuat sekaligus).
        md5, sha256 = hashlib.md5(), hashlib.sha256()
        first = b""
        for chunk in upload.chunks():
            if not first:
                first = chunk[:1]
            md5.update(chunk)
            sha256.update(chunk)
        upload.seek(0)
        self.checksums = {"size": upload.size, "md5": md5.hexdigest(), "sha256": sha256.hexdigest()}
        self.first_byte = first[0] if first else None
        return upload

    def clean(self):
        cleaned = super().clean()
        board, version = cleaned.get("board"), cleaned.get("version")
        if cleaned.get("file") and board and board != Board.SIMULATED and self.first_byte != ESP_IMAGE_MAGIC:
            self.add_error("file", "This is not an ESP firmware image (the first byte must be 0xE9).")
        if board and version and FirmwarePackage.objects.filter(
            uploaded_by=self.user, board=board, version=version
        ).exists():
            self.add_error("version", "You already uploaded this version for this board.")
        return cleaned

    def save(self, commit=True):
        package = super().save(commit=False)
        package.uploaded_by = self.user
        package.size = self.checksums["size"]
        package.md5 = self.checksums["md5"]
        package.sha256 = self.checksums["sha256"]
        if commit:
            package.save()
        return package


class PackageChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, package):
        return f"{package.version} — {package.title} ({package.size // 1024} KB)"


class StartUpdateForm(BootstrapFormMixin, forms.Form):
    package = PackageChoiceField(queryset=FirmwarePackage.objects.none(), empty_label="— Firmware version —")

    def __init__(self, *args, packages, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["package"].queryset = packages
