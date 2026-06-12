import threading
import requests
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from monitor.models import PunchEvent

import logging

logger = logging.getLogger('monitor')

def send_webhook_async(url, payload, headers):
    try:
        # Use reasonable timeouts (5s connect, 10s read) so we do not block threads forever
        response = requests.post(url, json=payload, headers=headers, timeout=(5, 10))
        logger.info(f"[ERP Webhook] Status: {response.status_code} for serial {payload.get('serial_no')}")
    except Exception as e:
        logger.error(f"[ERP Webhook] Error sending webhook: {e}")

from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

@receiver(post_save, sender=PunchEvent)
def handle_new_punch(sender, instance, created, **kwargs):
    if not created:
        return

    # Log punch event to dedicated logs/punches.log
    punches_logger = logging.getLogger('punches')
    local_time_str = timezone.localtime(instance.time).strftime('%Y-%m-%d %H:%M:%S')
    punches_logger.info(f"ID: {instance.employee_id_str} | Name: {instance.name} | Time: {local_time_str} | Mode: {instance.verify_mode or 'Unknown'}")

    webhook_url = getattr(settings, 'ERP_WEBHOOK_URL', None)
    if not webhook_url:
        return

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Natdemy-Attendance-System/1.0"
    }

    token = getattr(settings, 'ERP_WEBHOOK_TOKEN', None)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "serial_no": instance.serial_no,
        "employee_id": instance.employee_id_str,
        "name": instance.name,
        "time": instance.time.isoformat(),
        "verify_mode": instance.verify_mode or "Unknown",
    }

    # Dispatch to background thread so it does not block the database save operation or monitor loop
    thread = threading.Thread(
        target=send_webhook_async,
        args=(webhook_url, payload, headers),
        daemon=True
    )
    thread.start()

    # Broadcast to WebSocket consumers via Redis channel layer
    channel_layer = get_channel_layer()
    if channel_layer:
        async_to_sync(channel_layer.group_send)(
            "attendance_events",
            {
                "type": "attendance_punch",
                "data": payload,
            }
        )
