from django.urls import path

from . import views

app_name = "rules"

urlpatterns = [
    path("", views.RuleListView.as_view(), name="list"),
    # Langkah 1: pilih model rule engine. Langkah 2: form model itu.
    path("new/", views.RuleModelPickerView.as_view(), name="new"),
    path("new/<slug:model>/", views.eca_create_or_planned, name="new_model"),
    path("<int:pk>/", views.RuleDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.EcaRuleFormView.as_view(), name="edit"),
    path("<int:pk>/toggle/", views.RuleToggleView.as_view(), name="toggle"),
    path("<int:pk>/delete/", views.RuleDeleteView.as_view(), name="delete"),
    path("alarms/", views.AlarmListView.as_view(), name="alarms"),
    path("alarms/<int:pk>/acknowledge/", views.AlarmActionView.as_view(action="acknowledge"), name="alarm_ack"),
    path("alarms/<int:pk>/clear/", views.AlarmActionView.as_view(action="clear"), name="alarm_clear"),
]
