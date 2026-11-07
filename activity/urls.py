from django.urls import path

from . import views

app_name = "activity"

urlpatterns = [
    path("", views.ActivityListView.as_view(), name="list"),
    path("export.xlsx", views.ActivityExportView.as_view(), name="export"),
]
