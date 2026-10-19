# Tahap 14: penjadwalan aturan (ECA event "Schedule" dan rule chain Input "On a schedule").
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("rules", "0003_flow_rule_chain"),
    ]

    operations = [
        migrations.CreateModel(
            name="RuleSchedule",
            fields=[
                ("rule", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True,
                                              related_name="schedule", serialize=False, to="rules.rule")),
                ("kind", models.CharField(choices=[("DAILY", "At set times"), ("INTERVAL", "Every N minutes"),
                                                   ("ONCE", "Once")], default="DAILY", max_length=10,
                                          verbose_name="repeat")),
                ("times", models.CharField(blank=True, max_length=200, verbose_name="at")),
                ("weekdays", models.CharField(default="0123456", max_length=7, verbose_name="on")),
                ("interval_minutes", models.PositiveIntegerField(blank=True, null=True, verbose_name="every (minutes)")),
                ("run_at", models.DateTimeField(blank=True, null=True, verbose_name="on (date and time)")),
                ("active_for_seconds", models.PositiveIntegerField(default=0, verbose_name="keep active for (seconds)")),
                ("next_run_at", models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="next run")),
                ("last_run_at", models.DateTimeField(blank=True, null=True, verbose_name="last run")),
            ],
            options={"verbose_name": "rule schedule", "verbose_name_plural": "rule schedules"},
        ),
        migrations.AlterField(
            model_name="ecaspec",
            name="event",
            field=models.CharField(choices=[("TELEMETRY", "New data for a variable in the conditions"),
                                            ("INACTIVITY", "Device sends no data"), ("SCHEDULE", "On a schedule")],
                                   default="TELEMETRY", max_length=12, verbose_name="event"),
        ),
        migrations.AddField(
            model_name="flowspec",
            name="trigger",
            field=models.CharField(choices=[("TELEMETRY", "Telemetry messages"), ("SCHEDULE", "On a schedule")],
                                   default="TELEMETRY", max_length=10, verbose_name="input"),
        ),
        migrations.AlterField(
            model_name="ruleexecution",
            name="transition",
            field=models.CharField(choices=[("ACTIVATE", "Activated"), ("CLEAR", "Cleared"), ("RUN", "Ran actions"),
                                            ("SKIPPED", "Skipped")], max_length=10, verbose_name="transition"),
        ),
    ]
