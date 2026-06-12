# ERP Integration Guide (Django Rest Framework)

This document describes how to configure your Django REST Framework (DRF) ERP to receive real-time attendance punch events forwarded from the local Hikvision monitor server.

---

## 1. ERP-Side Implementation Steps

### Step A: Update your Database Models (`models.py`)
Add or update the following models inside your ERP's Django app:

```python
from django.db import models

class Employee(models.Model):
    # Your existing employee fields...
    name = models.CharField(max_length=150)
    
    # Store the unique Card/Biometric ID from the Hikvision device
    biometric_id = models.CharField(
        max_length=50, 
        unique=True, 
        null=True, 
        blank=True,
        help_text="Hikvision Device EmployeeNo / Card Number"
    )

    def __str__(self):
        return self.name


class ERPAttendanceLog(models.Model):
    employee = models.ForeignKey(
        Employee, 
        on_delete=models.CASCADE, 
        related_name='attendance_logs'
    )
    timestamp = models.DateTimeField()
    
    # Stores the serial_no to prevent duplicates if the webhook is retried
    serial_no = models.IntegerField(unique=True)
    
    verify_mode = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employee.name} at {self.timestamp}"
```

---

### Step B: Create a Serializer (`serializers.py`)
Create a serializer to validate incoming request data from the local server:

```python
from rest_framework import serializers
from .models import Employee, ERPAttendanceLog

class AttendanceWebhookSerializer(serializers.Serializer):
    serial_no = serializers.IntegerField()
    employee_id = serializers.CharField(max_length=50)
    name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    time = serializers.DateTimeField()
    verify_mode = serializers.CharField(max_length=50, required=False)

    def save(self):
        validated_data = self.validated_data
        emp_id = validated_data['employee_id']
        
        # 1. Match the employee by their biometric_id
        try:
            employee = Employee.objects.get(biometric_id=emp_id)
        except Employee.DoesNotExist:
            raise serializers.ValidationError(
                f"Employee with biometric ID '{emp_id}' not found in ERP."
            )

        # 2. Prevent duplicate entries using update_or_create on serial_no
        log, created = ERPAttendanceLog.objects.update_or_create(
            serial_no=validated_data['serial_no'],
            defaults={
                'employee': employee,
                'timestamp': validated_data['time'],
                'verify_mode': validated_data.get('verify_mode', 'Unknown')
            }
        )
        return log
```

---

### Step C: Create the API Endpoint View (`views.py`)
Handle the incoming webhook `POST` request:

```python
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from .serializers import AttendanceWebhookSerializer

class ReceiveAttendanceWebhookView(APIView):
    # Set to AllowAny if testing locally, or use IsAuthenticated with a token
    permission_classes = [AllowAny] 

    def post(self, request, *args, **kwargs):
        serializer = AttendanceWebhookSerializer(data=request.data)
        if serializer.is_valid():
            try:
                log = serializer.save()
                return Response({
                    "success": True, 
                    "message": f"Successfully registered punch for {log.employee.name}"
                }, status=status.HTTP_201_CREATED)
            except serializers.ValidationError as e:
                return Response({
                    "success": False, 
                    "errors": e.detail
                }, status=status.HTTP_400_BAD_REQUEST)
        
        return Response({
            "success": False, 
            "errors": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
```

---

### Step D: Register the API Endpoint Router (`urls.py`)
Add the route to your ERP URLs:

```python
from django.urls import path
from .views import ReceiveAttendanceWebhookView

urlpatterns = [
    # Webhook receiving endpoint URL
    path('api/attendance/webhook/', ReceiveAttendanceWebhookView.as_view(), name='attendance_webhook'),
]
```

---

## 2. Local Attendance Server Configuration

Once you have deployed the endpoint in your ERP system, configure the local dashboard server to start forwarding events:

1. Open **`attendance_system/settings.py`** on this machine.
2. Set the `ERP_WEBHOOK_URL` to your ERP API path:
   ```python
   ERP_WEBHOOK_URL = "http://your-erp-domain-or-ip/api/attendance/webhook/"
   ERP_WEBHOOK_TOKEN = None # Add auth token if configured
   ```
3. Restart your local server backend:
   ```bash
   python manage.py runserver
   ```
