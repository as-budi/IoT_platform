# Tahap 19 (perbaikan): constraint "user XOR group" diganti "bukan user DAN group".
# Lihat komentar di DeviceShare.Meta.constraints: XOR menolak UPDATE ... SET NULL
# yang dijalankan Django di MySQL sebelum menghapus share saat grup/pengguna dihapus.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sharing', '0001_initial'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='deviceshare',
            name='deviceshare_user_xor_group',
        ),
        migrations.AddConstraint(
            model_name='deviceshare',
            constraint=models.CheckConstraint(condition=models.Q(('user__isnull', True), ('group__isnull', True), _connector='OR'), name='deviceshare_not_user_and_group'),
        ),
    ]
