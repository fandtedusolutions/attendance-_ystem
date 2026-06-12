from django.urls import path
from .views import ReceiveAttendanceWebhookView, dashboard_view

urlpatterns = [
    path('api/attendance/webhook/', ReceiveAttendanceWebhookView.as_view(), name='attendance_webhook'),
    path('', dashboard_view, name='dashboard'),
]
