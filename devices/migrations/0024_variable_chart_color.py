from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0023_device_last_heard_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="variable",
            name="chart_color",
            field=models.CharField(blank=True, default="", max_length=7, verbose_name="chart color"),
        ),
    ]
