from django.http import JsonResponse
from apps.management.models import Device
from apps.widgets.models import DeviceData

import math
import random

def generate_random_location(lat, lng, radius=0.001):
    """
    Based on latest location (lat, lng), generate new location in the range of radius
    by degree, 0.001 = 111 meters
    """
    angle = random.uniform(0, 2 * math.pi)
    offset_lat = radius * math.cos(angle)
    offset_lng = radius * math.sin(angle)

    new_lat = lat + offset_lat
    new_lng = lng + offset_lng

    return new_lat, new_lng


def simulate_device_movement(request):
    devices = Device.objects.all()

    for device in devices:
        last_path = device.data.last()

        if last_path:
            new_lat, new_lng = generate_random_location(last_path.latitude, last_path.longitude)
            # update new location
            DeviceData.objects.create(
                device=device,
                latitude=new_lat,
                longitude=new_lng
            )
        else:
            DeviceData.objects.create(
                device=device,
                latitude=40.803237369807576,
                longitude=-77.88694790309172
            )

    return JsonResponse({'status': 'success', 'message': 'Devices moved successfully'})
