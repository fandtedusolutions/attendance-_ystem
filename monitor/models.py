from django.db import models

class Employee(models.Model):
    employee_id = models.CharField(max_length=50, primary_key=True)
    name = models.CharField(max_length=150, blank=True, default='')
    gender = models.CharField(max_length=20, blank=True, default='Unknown')
    user_type = models.CharField(max_length=50, blank=True, default='Unknown')
    num_fp = models.IntegerField(default=0)
    num_face = models.IntegerField(default=0)
    group_id = models.CharField(max_length=50, blank=True, default='')
    face_url = models.CharField(max_length=500, blank=True, default='')
    last_punch_time = models.DateTimeField(null=True, blank=True)
    
    def __str__(self):
        return f"{self.name or 'Unknown'} ({self.employee_id})"

class PunchEvent(models.Model):
    serial_no = models.IntegerField(unique=True)
    employee = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='punches')
    employee_id_str = models.CharField(max_length=50)
    name = models.CharField(max_length=150, blank=True, default='')
    time = models.DateTimeField()
    verify_mode = models.CharField(max_length=100, blank=True, default='')
    logged_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Punch {self.serial_no} - {self.name} ({self.time})"

class SystemStatus(models.Model):
    key = models.CharField(max_length=50, primary_key=True)
    value = models.CharField(max_length=255, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.key}: {self.value}"
