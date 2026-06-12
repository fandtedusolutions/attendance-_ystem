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
        
        try:
            employee = Employee.objects.get(biometric_id=emp_id)
        except Employee.DoesNotExist:
            raise serializers.ValidationError(
                f"Employee with biometric ID '{emp_id}' not found in ERP."
            )

        log, created = ERPAttendanceLog.objects.update_or_create(
            serial_no=validated_data['serial_no'],
            defaults={
                'employee': employee,
                'timestamp': validated_data['time'],
                'verify_mode': validated_data.get('verify_mode', 'Unknown')
            }
        )
        return log
