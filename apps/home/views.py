import json

from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.home.models import Ride
from apps.management.models import Device
from apps.management.onem2m_service import update_lock_module


# Create your views here.
@login_required(login_url="/login/")
def home(request):
    return render(request, 'home/home.html')

@login_required(login_url="/login/")
def ride1_view(request):
    return render(request, 'home/ride-1.html')

def ride2_view(request):
    if not request.session.get('bike_number'):
        return redirect('home')
    return render(request, 'home/ride-2.html')

def ride3_view(request):
    bike_id = request.session.get('bike_number')

    if not bike_id:
        return redirect('home')

    # Send PUT to update Unlock resource here
    device = get_object_or_404(Device, hardware_id=bike_id)
    ae_rn = device.ae_rn
    originator = f"C{device.hardware_id}"
    update_lock_module(ae_rn, originator, status=False)

    # Create a new ride record
    new_ride = Ride.objects.create(
        user=request.user,
        device=device,
        start_time=timezone.now()
    )
    new_ride.save()

    device.status = 'active'
    device.save()

    request.session.pop('bike_number', None)
    return render(request, 'home/ride-3.html')


def ride4_view(request):
    ride = Ride.objects.filter(user=request.user).order_by('-start_time').first()

    if ride and ride.end_time:
        duration_seconds = (ride.end_time - ride.start_time).total_seconds()
        hours, remainder = divmod(duration_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        formatted_duration = f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}"
    else:
        formatted_duration = "00:00:00"

    return render(request, 'home/ride-4.html', {'ride_duration': formatted_duration})

@login_required(login_url="/login/")
def validate_bike_number(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        bike_number = data.get('bike_number')

        try:
            device = Device.objects.get(hardware_id=bike_number, user=None)
            device.user = request.user
            device.save()

            # Session flag
            request.session['bike_number'] = bike_number

            return JsonResponse({'valid': True})
        except Device.DoesNotExist:
            return JsonResponse({'valid': False})

    return JsonResponse({'valid': False})


@login_required(login_url="/login/")
def get_user_ride_info(request):
    user = request.user
    latest_ride = user.rides.order_by('-start_time').first()

    if latest_ride and latest_ride.end_time is None:
        # current ride
        duration_seconds = (timezone.now() - latest_ride.start_time).total_seconds()
        data = {
            'device_name': latest_ride.device.name,
            'status': 'in_use',
            'duration_seconds': duration_seconds,
            'ride_history': list(user.rides.all().values())
        }
    else:
        # No riding
        data = {
            'status': 'no_active_ride',
            'ride_history': list(user.rides.all().values())  # Return all user's ride
        }

    return JsonResponse(data)

@login_required(login_url="/login/")
@require_POST
def end_ride(request):
    # Fetch Newest Ride Record
    latest_ride = request.user.rides.filter(end_time__isnull=True).order_by('-start_time').first()

    if latest_ride:
        device = latest_ride.device
        ae_rn = device.ae_rn
        originator = f"C{device.hardware_id}"

        # Lock Bike
        update_lock_module(ae_rn, originator, status=True)

        # Save end time for record
        latest_ride.end_time = timezone.now()
        latest_ride.save()

        # Set device current user to None for next use
        device.user = None
        device.status = 'inactive'
        device.save()

        return redirect('ride-4')

    return redirect('home')
