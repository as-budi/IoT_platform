from django.urls import path

from . import views

app_name = "rules"

urlpatterns = [
    path("", views.RuleListView.as_view(), name="list"),
    # "Versi data" untuk pemeriksaan cadangan pembaruan langsung (auto_refresh.js).
    path("updates/", views.updates, {"kind": "rules"}, name="updates"),
    path("<int:pk>/updates/", views.updates, {"kind": "rule"}, name="detail_updates"),
    path("alarms/updates/", views.updates, {"kind": "alarms"}, name="alarms_updates"),
    # Langkah 1: pilih model rule engine. Langkah 2: form model itu.
    path("new/", views.RuleModelPickerView.as_view(), name="new"),
    path("new/<slug:model>/", views.create_rule, name="new_model"),
    path("<int:pk>/", views.RuleDetailView.as_view(), name="detail"),
    path("<int:pk>/evaluation/", views.rule_evaluation, name="evaluation"),
    path("<int:pk>/edit/", views.edit_rule, name="edit"),
    path("<int:pk>/toggle/", views.RuleToggleView.as_view(), name="toggle"),
    path("<int:pk>/delete/", views.RuleDeleteView.as_view(), name="delete"),
    path("alarms/", views.AlarmListView.as_view(), name="alarms"),
    path("alarms/<int:pk>/acknowledge/", views.AlarmActionView.as_view(action="acknowledge"), name="alarm_ack"),
    path("alarms/<int:pk>/clear/", views.AlarmActionView.as_view(action="clear"), name="alarm_clear"),
]
