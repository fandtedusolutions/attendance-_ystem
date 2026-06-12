import json
from channels.generic.websocket import AsyncWebsocketConsumer

class AttendanceConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.group_name = "attendance_events"

        # Join group
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

        await self.accept()

    async def disconnect(self, close_code):
        # Leave group
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    async def attendance_punch(self, event):
        """
        Receives new punch events and forwards them to the client.
        """
        await self.send(text_data=json.dumps({
            "type": "punch_event",
            "data": event["data"]
        }))

    async def monitor_status_change(self, event):
        """
        Receives monitor heartbeats, status updates, or errors and forwards them.
        """
        await self.send(text_data=json.dumps({
            "type": "status_update",
            "data": event["data"]
        }))
