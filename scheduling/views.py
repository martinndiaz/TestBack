from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from datetime import datetime, timedelta
from rest_framework.permissions import AllowAny
from datetime import date
from rest_framework.decorators import api_view, authentication_classes, permission_classes






from users.models import Patient
from doctors.models import Kinesiologist
from .models import Appointment, Availability
from .serializers import (
    AppointmentSerializer,
    AvailabilitySerializer,
    KinesiologistSummarySerializer,
    TimeSlotSerializer,
)

SLOT_MINUTES = 45

class AvailabilityListCreateView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, kinesiologist_id: int):
        kinesiologist = get_object_or_404(Kinesiologist.objects.select_related('user'), pk=kinesiologist_id)

        availability_qs = (
            Availability.objects.filter(kinesiologist=kinesiologist)
            .order_by('day', 'start_time')
        )
        appointments_qs = (
            Appointment.objects.filter(kinesiologist=kinesiologist)
            .select_related('patient_name__user', 'kinesiologist__user')
            .order_by('date', 'start_time')
        )

        availability = AvailabilitySerializer(availability_qs, many=True)
        appointments = AppointmentSerializer(appointments_qs, many=True)
        kinesiologist_data = KinesiologistSummarySerializer(kinesiologist)

        return Response(
            {
                "kinesiologist": kinesiologist_data.data,
                "availability": availability.data,
                "appointments": appointments.data,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request, kinesiologist_id: int):
        kinesiologist = get_object_or_404(Kinesiologist.objects.select_related('user'), pk=kinesiologist_id)

        if not (
            request.user.is_superuser
            or request.user == kinesiologist.user
        ):
            return Response(
                {
                    "status": False,
                    "message": "No tiene permisos para registrar este horario.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = AvailabilitySerializer(
            data=request.data,
            context={'kinesiologist': kinesiologist},
        )
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                availability = serializer.save(kinesiologist=kinesiologist)
        except ValidationError as exc:
            message = getattr(exc, 'message', None) or getattr(exc, 'messages', [str(exc)])[0]
            return Response(
                {
                    "status": False,
                    "message": message,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except IntegrityError:
            return Response(
                {
                    "status": False,
                    "message": "No fue posible guardar el horario. Intente nuevamente.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "status": True,
                "message": "Horario registrado correctamente.",
                "availability": AvailabilitySerializer(availability).data,
            },
            status=status.HTTP_201_CREATED,
        )


class AppointmentCreateView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, kinesiologist_id: int):
        kinesiologist = get_object_or_404(Kinesiologist.objects.select_related('user'), pk=kinesiologist_id)

        serializer = AppointmentSerializer(
            data=request.data,
            context={'kinesiologist': kinesiologist},
        )
        serializer.is_valid(raise_exception=True)

        patient = serializer.validated_data['patient_name']
        is_authorized = request.user.is_superuser or request.user == patient.user or request.user == kinesiologist.user
        if not is_authorized:
            return Response(
                {
                    "status": False,
                    "message": "No tiene permisos para agendar esta hora médica.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            with transaction.atomic():
                appointment = serializer.save()
        except ValidationError as exc:
            message = getattr(exc, 'message', None) or getattr(exc, 'messages', [str(exc)])[0]
            return Response(
                {
                    "status": False,
                    "message": message,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except IntegrityError:
            return Response(
                {
                    "status": False,
                    "message": "No fue posible agendar la hora médica. Intente nuevamente.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        appointment_data = AppointmentSerializer(appointment).data
        return Response(
            {
                "status": True,
                "message": "Hora médica reservada correctamente.",
                "appointment": appointment_data,
            },
            status=status.HTTP_201_CREATED,
        )


class KinesiologistAvailableSlotsView(APIView):
    """
    Devuelve los horarios disponibles de un kinesiólogo para una fecha dada.
    GET /api/kinesiologists/<kinesiologist_id>/slots/?date=YYYY-MM-DD
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, kinesiologist_id):
        date_str = request.query_params.get("date")
        if not date_str:
            return Response(
                {"detail": "Parámetro 'date' es obligatorio (YYYY-MM-DD)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response(
                {"detail": "Formato de fecha inválido. Usa YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # día de la semana según tu modelo Availability (0 = lunes, 6 = domingo)
        day_of_week = target_date.weekday()

        # 1) Buscamos disponibilidad base del kinesiólogo para ese día
        availability_qs = Availability.objects.filter(
            kinesiologist_id=kinesiologist_id,
            day=day_of_week,
        )

        if not availability_qs.exists():
            # no tiene horario ese día
            return Response([], status=status.HTTP_200_OK)

        # 2) Traemos las citas ya reservadas para ese día
        existing_appointments = Appointment.objects.filter(
            kinesiologist_id=kinesiologist_id,
            date=target_date,
        )

        slot_length = timedelta(minutes=SLOT_MINUTES)
        slots = []

        for avail in availability_qs:
            current_start = datetime.combine(target_date, avail.start_time)
            avail_end_dt = datetime.combine(target_date, avail.end_time)

            while current_start + slot_length <= avail_end_dt:
                current_end = current_start + slot_length

                # ¿este bloque se superpone con una cita ya reservada?
                overlap = existing_appointments.filter(
                    start_time__lt=current_end.time(),
                    end_time__gt=current_start.time(),
                ).exists()

                if not overlap:
                    slots.append(
                        {
                            "date": target_date,
                            "start_time": current_start.time(),
                            "end_time": current_end.time(),
                            "datetime": current_start,
                        }
                    )

                current_start += slot_length

        serializer = TimeSlotSerializer(slots, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def patient_appointments_history(request):
    patient = Patient.objects.get(user=request.user)

    qs = (
        Appointment.objects
        .filter(patient_name=patient)
        .select_related("kinesiologist__user")
        .order_by("-date", "-start_time")
    )

    today = date.today()
    data = []

    for a in qs:
        status = "completed" if a.date < today else "pending"

        data.append({
            "id": a.id,
            "date": a.date.strftime("%Y-%m-%d"),
            "time": a.start_time.strftime("%H:%M"),
            "treatment": "Sesión de kinesiología",
            "kinesiologist": a.kinesiologist.user.get_full_name()
                or a.kinesiologist.user.username,
            "status": status,
        })

    return Response(data)