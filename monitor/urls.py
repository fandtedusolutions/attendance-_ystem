from django.urls import path
from monitor import views

app_name = 'monitor'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('report/', views.day_report_page, name='day_report_page'),
    path('api/report/day/', views.api_day_report, name='api_day_report'),
    path('api/events/', views.api_events, name='api_events'),
    path('api/sync/', views.api_sync_users, name='api_sync_users'),
    path('api/recover/', views.api_recover_today, name='api_recover_today'),
    path('api/broadcast/', views.api_broadcast, name='api_broadcast'),
    path('api/export/', views.export_attendance, name='api_export'),
    path('api/webhook/resend/', views.api_resend_webhook, name='api_resend_webhook'),
    path('api/webhook/mode/', views.api_webhook_mode, name='api_webhook_mode'),
    path('api/webhook/pending/', views.api_webhook_pending, name='api_webhook_pending'),
    path('api/webhook/send-single/', views.api_send_single_punch, name='api_send_single_punch'),
]
