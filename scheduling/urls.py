from django.urls import path
from .views import AppointmentCreateView, AvailabilityListCreateView, KinesiologistAvailableSlotsView

app_name = "scheduling"

urlpatterns = [
    path('kinesiologists/<int:kinesiologist_id>/availability',AvailabilityListCreateView.as_view(), name='kinesiologist-availability',),
    path('kinesiologists/<int:kinesiologist_id>/appointments',AppointmentCreateView.as_view(),name='kinesiologist-appointments',),
    path("kinesiologists/<int:kinesiologist_id>/slots/",KinesiologistAvailableSlotsView.as_view(),name="kinesiologist-slots",)
]
