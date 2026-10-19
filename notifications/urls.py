from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("telegram/connect/", views.TelegramConnectView.as_view(), name="telegram_connect"),
    path("telegram/test/", views.TelegramTestView.as_view(), name="telegram_test"),
    path("telegram/disconnect/", views.TelegramDisconnectView.as_view(), name="telegram_disconnect"),
]
