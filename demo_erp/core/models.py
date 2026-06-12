from django.db import models

class Employee(models.Model):
    name = models.CharField(max_length=150)
    biometric_id = models.CharField(
        max_length=50, 
        unique=True, 
        null=True, 
        blank=True,
        help_text="Hikvision Device EmployeeNo / Card Number"
    )

    def __str__(self):
        return f"{self.name} ({self.biometric_id})"


class ERPAttendanceLog(models.Model):
    employee = models.ForeignKey(
        Employee, 
        on_delete=models.CASCADE, 
        related_name='attendance_logs'
    )
    timestamp = models.DateTimeField()
    serial_no = models.IntegerField(unique=True)
    verify_mode = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employee.name} at {self.timestamp} (Serial: {self.serial_no})"
