import time
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone, dateparse
from django.views.decorators.csrf import csrf_exempt

from apps.management.models import Device
from apps.management.onem2m_service import logger
from apps.widgets.models import DeviceData, MeshConnectivity
from core.decorators import superuser_required
from datetime import timezone as dt_timezone

"""
MAP Widget
"""
@login_required(login_url="/login/")
@superuser_required
def device_map(request):

    devices = Device.objects.all().prefetch_related('data')

    # QuerySet to JSON
    devices_data = []
    for device in devices:
        device_info = {
            'id': device.id,
            'name': device.name,
            'latitude': device.data.last().latitude if device.data.exists() else 'null',
            'longitude': device.data.last().longitude if device.data.exists() else 'null',
            'paths': [{'latitude': path.latitude, 'longitude': path.longitude} for path in device.data.all()]
        }
        devices_data.append(device_info)

    context = {'devices': devices_data}
    return render(request, 'widgets/map.html', context)


"""
LOG Widget
"""
@login_required(login_url="/login/")
@superuser_required
def device_logs(request):
    return render(request, 'widgets/logs.html')

"""
MESH Widget
"""
@login_required(login_url="/login/")
@superuser_required
def device_mesh(request):
    return render(request, 'widgets/mesh_tree.html')

"""
Data API
"""
def device_path(request, device_id):
    data = DeviceData.objects.filter(device_id=device_id).order_by('timestamp')
    # if data exists, return the latest position
    if data.exists():
        latest_position = {
            'latitude': data.last().latitude,
            'longitude': data.last().longitude
        }
    else:
        latest_position = {'latitude': None, 'longitude': None}

    # check full path parameter
    if 'full_path' in request.GET:
        path_data = [{'latitude': path.latitude, 'longitude': path.longitude} for path in data]
        return JsonResponse({'path': path_data, 'latest_position': latest_position}, safe=False)

    # return latest position default
    return JsonResponse(latest_position, safe=False)

@csrf_exempt
def device_data(request):
    if request.method == 'GET':
        devices = Device.objects.prefetch_related('data')  # Prefetch related data

        data_list = []
        for device in devices:
            latest_data = device.data.last()  # Get the latest path

            latest_ride = device.rides.filter(end_time__isnull=True).order_by('-start_time').first()

            if latest_data:
                # Calculate the duration of the current ride if active
                if latest_ride and latest_ride.start_time and not latest_ride.end_time:
                    ride_duration_seconds = (timezone.now() - latest_ride.start_time).total_seconds()
                else:
                    ride_duration_seconds = None

                data = {
                    'device_name': device.name,
                    'current_rider': device.user.username if device.user else 'None',
                    'ride_duration': f"{int(ride_duration_seconds // 3600):02}:{int((ride_duration_seconds % 3600) // 60):02}:{int(ride_duration_seconds % 60):02}" if ride_duration_seconds is not None else 'None',
                    'temperature': latest_data.temperature,
                    'speed': latest_data.speed,
                    'latitude': latest_data.latitude,
                    'longitude': latest_data.longitude,
                }
                data_list.append(data)

        return JsonResponse({'status': 'success', 'data': data_list}, status=200)

    return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=400)


last_update_time = timezone.now()
POLLING_TIMEOUT = 30

@csrf_exempt
def mesh_network_data(request):
    global last_update_time
    try:
        client_last_update = float(request.GET.get('last_update', '0'))

        start_time = time.time()

        while True:
            current_last_update = last_update_time.timestamp()

            if current_last_update > client_last_update:
                # update and return nodes
                devices = Device.objects.all()
                mesh_connections = MeshConnectivity.objects.all()

                nodes = []
                links = []

                # add 'sink' node
                nodes.append({
                    'id': '00000',
                    'name': 'ACME',
                    'latitude': 40.798224,
                    'longitude': -77.857236
                })

                # add all device as node
                for device in devices:
                    nodes.append({
                        'id': device.hardware_id,
                        'name': device.name,
                        'latitude': device.data.last().latitude,
                        'longitude': device.data.last().longitude
                    })

                for connection in mesh_connections:
                    if connection.parent_id:
                        links.append({
                            'source': connection.device.hardware_id,
                            'target': connection.parent_id,
                            'stnr': connection.stnr
                        })

                response = {
                    'nodes': nodes,
                    'links': links,
                    'last_update': current_last_update
                }
                return JsonResponse(response)

            elif time.time() - start_time > POLLING_TIMEOUT:
                response = {'last_update': current_last_update}
                return JsonResponse(response)

            else:
                time.sleep(1)

    except Exception as e:
        logger.error(f"Error in mesh_network_data: {e}")
        return JsonResponse({'error': 'Internal Server Error'}, status=500)


@csrf_exempt
def device_speed_data(request, device_id):
    if request.method == 'GET':
        device = get_object_or_404(Device, id=device_id)
        data_points = device.data.order_by('-timestamp')[:10]  # 10 data from recent
        data = [
            {
                'timestamp': data_point.timestamp.isoformat(),
                'speed': data_point.speed
            } for data_point in reversed(data_points)  # time sequence
        ]
        return JsonResponse({'status': 'success', 'data': data}, status=200)
    return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=400)


@csrf_exempt
def device_latest_speed(request, device_id):
    if request.method == 'GET':
        device = get_object_or_404(Device, id=device_id)
        since_timestamp_str = request.GET.get('since')
        latest_data = device.data.last()
        if latest_data:
            data_timestamp = latest_data.timestamp
            if since_timestamp_str:
                since_timestamp = dateparse.parse_datetime(since_timestamp_str)
                if since_timestamp is not None:
                    if timezone.is_naive(since_timestamp):

                        since_timestamp = timezone.make_aware(since_timestamp, timezone.get_current_timezone())
                    # translate to UTC
                    data_timestamp_utc = data_timestamp.astimezone(dt_timezone.utc)
                    since_timestamp_utc = since_timestamp.astimezone(dt_timezone.utc)

                    if data_timestamp_utc <= since_timestamp_utc:
                        return JsonResponse({'status': 'no_new_data'}, status=200)
                else:
                    return JsonResponse({'status': 'error', 'message': 'Invalid timestamp format'}, status=400)
            data = {
                'timestamp': data_timestamp.isoformat(),
                'speed': latest_data.speed
            }
            return JsonResponse({'status': 'success', 'data': data}, status=200)
        else:
            return JsonResponse({'status': 'error', 'message': 'No data found for device'}, status=404)
    return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=400)
