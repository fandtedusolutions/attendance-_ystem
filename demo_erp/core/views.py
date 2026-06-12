from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, serializers
from rest_framework.permissions import AllowAny
from django.shortcuts import render
from .models import ERPAttendanceLog
from .serializers import AttendanceWebhookSerializer

class ReceiveAttendanceWebhookView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = AttendanceWebhookSerializer(data=request.data)
        if serializer.is_valid():
            try:
                log = serializer.save()
                print(f"[Demo ERP] SUCCESS: Saved log for {log.employee.name} (Serial: {log.serial_no})")
                return Response({
                    "success": True, 
                    "message": f"Successfully registered punch for {log.employee.name}"
                }, status=status.HTTP_201_CREATED)
            except serializers.ValidationError as e:
                print(f"[Demo ERP] VALIDATION ERROR: {e.detail}")
                return Response({
                    "success": False, 
                    "errors": e.detail
                }, status=status.HTTP_400_BAD_REQUEST)
        
        print(f"[Demo ERP] SERIALIZER ERROR: {serializer.errors}")
        return Response({
            "success": False, 
            "errors": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)

def dashboard_view(request):
    logs = ERPAttendanceLog.objects.order_by('-timestamp')[:50]
    return render(request, 'core/dashboard.html', {'logs': logs})
