import json
import logging
import re

import requests
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.management.forms import DeviceForm, FirmwareUploadForm
from apps.management.models import Device, Firmware
from apps.management.onem2m_service import logger, generate_request_identifier, delete_device_ae
from apps.widgets.models import DeviceData, MeshConnectivity, Battery
from core import settings
from core.decorators import superuser_required


@login_required(login_url="/login/")
def root_view(request):
    if request.user.is_superuser:
        return redirect('management')
    else:
        return redirect('home')

@login_required(login_url="/login/")
@superuser_required
def index(request):

    form = DeviceForm()
    user_devices = Device.objects.filter(owner=request.user)
    context = {'devices': user_devices, 'form': form}

    return render(request, 'management/index.html', context)

@login_required(login_url="/login/")
@superuser_required
def device(request):

    form = DeviceForm()
    user_devices = Device.objects.filter(owner=request.user)
    lte_firmwares = Firmware.objects.filter(device_type='LTE')
    dectnr_firmwares = Firmware.objects.filter(device_type='DECT_NR')
    context = {
        'devices': user_devices,
        'form': form,
        'ONE_M2M_CSE_URL': settings.ONE_M2M_CSE_URL,
        'lte_firmwares': lte_firmwares,
        'dectnr_firmwares': dectnr_firmwares,
    }

    return render(request, 'management/device.html', context)

@login_required(login_url="/login/")
@superuser_required
def add_device(request):
    if request.method == 'POST':
        form = DeviceForm(request.POST)
        if form.is_valid():
            new_device = form.save(commit=False)
            new_device.owner = request.user  # Set the owner to the current user
            new_device.ae_rn = "ae_" + form.cleaned_data['hardware_id'].replace(" ","")
            new_device.save()
            return redirect('device')  # Redirect to the device list after saving
    else:
        form = DeviceForm()

    return render(request, 'management/device.html', {'form': form})

@login_required(login_url="/login/")
@superuser_required
def delete_device(request):
    if request.method == 'POST':
        device_ids = request.POST.get('device_ids')
        if device_ids:
            device_ids_list = device_ids.split(',')
            devices_to_delete = Device.objects.filter(id__in=device_ids_list, owner=request.user)
            for device in devices_to_delete:
                success = delete_device_ae(device)
                if success:
                    device.delete()
                else:
                    logger.error(f"Failed to delete device {device.name} from OneM2M.")
                    device.delete()

    return redirect('device')

@login_required
@superuser_required
def get_device_ride_info(request, device_id):
    device = get_object_or_404(Device, hardware_id=device_id)

    latest_ride = device.rides.order_by('-start_time').first()

    if latest_ride and latest_ride.end_time is None:
        # Current is riding
        duration_seconds = (timezone.now() - latest_ride.start_time).total_seconds()
        data = {
            'device_name': device.name,
            'status': 'in_use',
            'rider_name': latest_ride.user.username,
            'duration_seconds': duration_seconds,
            'ride_history': list(device.rides.all().values())  # Return All ride history for this device
        }
    else:
        # Not Used
        data = {
            'device_name': device.name,
            'status': 'available',
            'duration_seconds': 0,
            'ride_history': list(device.rides.all().values())
        }

    return JsonResponse(data)


@csrf_exempt
def receive_notification(request):

    if request.method == 'GET':
        return JsonResponse({"status": "success", "message": "Verification successful"}, status=200)

    if request.method == 'POST':
        try:

            content_type = request.headers.get('Content-Type', '')
            if 'application/json' in content_type or 'application/vnd.onem2m-res+json' in content_type:
                payload = json.loads(request.body)
            else:
                logger.error(f"Unsupported Content-Type: {content_type}")
                return JsonResponse({"status": "error", "message": "Unsupported Content-Type"}, status=415)

            logger.debug(f"Received notification payload: {payload}")

            if payload.get('m2m:sgn', {}).get('vrq', False):
                logger.info("Received verification request. Responding with 200 OK.")

                response = HttpResponse(content_type="application/json", status=200)
                response['X-M2M-RSC'] = '2000'  # Success response code for oneM2M
                response['X-M2M-RI'] = request.headers.get('X-M2M-RI', '')
                return response

            logger.info("Processing actual data notification...")

            notification = payload.get('m2m:sgn', {})
            nev = notification.get('nev', {})
            rep = nev.get('rep', {})

            for key, data in rep.items():
                if key == 'bdm:bikDt':
                    process_device_data(data)
                elif key == 'bdm:msCoy':
                    process_mesh_connectivity(data)
                elif key == 'cod:bat':
                    process_battery_level(data)
                else:
                    logger.warning(f"Unknown data type: {key}")

            return JsonResponse({"status": "success"}, status=200)

        except json.JSONDecodeError:
            logger.error("Invalid JSON received")
            return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

        except Exception as e:
            logger.exception(f"Error processing notification: {e}")
            return JsonResponse({"status": "error", "message": str(e)}, status=500)

def process_device_data(data):
    pi = data.get('pi', '')

    if not pi or not pi.lower().startswith('c'):
        logger.error(f"Invalid or missing 'pi' in device data: {pi}")
        return

    hardware_id = pi[1:]

    try:
        device = Device.objects.get(hardware_id=hardware_id)
    except Device.DoesNotExist:
        logger.error(f"No device found with hardware_id: {hardware_id}")
        return

    latitude = data.get('latie')
    longitude = data.get('longe')
    temperature = data.get('tempe')
    speed = data.get('speed')
    acceleration = data.get('accel')

    if any([temperature is not None, speed is not None, latitude is not None, longitude is not None,
            acceleration is not None]):
        DeviceData.objects.create(
            device=device,
            temperature=temperature,
            speed=speed,
            latitude=latitude,
            longitude=longitude,
            acceleration=acceleration,
        )
        logger.info(f"DeviceData updated for device '{device.name}': {data}")
    else:
        logger.warning(f"No relevant sensor data found in device data: {data}")

def process_mesh_connectivity(data):
    pi = data.get('pi', '')

    if not pi or not pi.lower().startswith('c'):
        logger.error(f"Invalid or missing 'pi' in mesh connectivity data: {pi}")
        return

    hardware_id = pi[1:]

    try:
        device = Device.objects.get(hardware_id=hardware_id)
    except Device.DoesNotExist:
        logger.error(f"No device found with hardware_id: {hardware_id}")
        return

    parent_id = data.get('neibo')
    stnr = data.get('stnr')

    mesh_connectivity, created = MeshConnectivity.objects.get_or_create(device=device)
    mesh_connectivity.parent_id = parent_id
    mesh_connectivity.stnr = stnr
    mesh_connectivity.save()

    logger.info(f"MeshConnectivity updated for device '{device.name}': {data}")

def process_battery_level(data):
    pi = data.get('pi', '')

    if not pi or not pi.lower().startswith('c'):  # case-insensitive
        logger.error(f"Invalid or missing 'pi' in battery data: {pi}")
        return

    hardware_id = pi[1:]

    try:
        device = Device.objects.get(hardware_id=hardware_id)
    except Device.DoesNotExist:
        logger.error(f"No device found with hardware_id: {hardware_id}")
        return

    battery_percentage = data.get('lvl')

    if battery_percentage is not None:
        Battery.objects.create(
            device=device,
            battery_percentage=battery_percentage
        )
        logger.info(f"BatteryLevel updated for device '{device.name}': {data}")
    else:
        logger.warning(f"No 'battery_level' found in battery data: {data}")

# -----------------------------Firmware Update----------------------------------------

@login_required
@superuser_required
def firmware_list(request):
    lte_firmwares = Firmware.objects.filter(device_type='LTE').order_by('-upload_date')
    dectnr_firmwares = Firmware.objects.filter(device_type='DECT_NR').order_by('-upload_date')

    context = {
        'lte_firmwares': lte_firmwares,
        'dectnr_firmwares': dectnr_firmwares,
    }
    return render(request, 'management/firmware_list.html', context)

@login_required
@superuser_required
def upload_firmware(request):
    if request.method == 'POST':
        form = FirmwareUploadForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect('firmware_list')
    else:
        form = FirmwareUploadForm()
    return render(request, 'management/upload_firmware.html', {'form': form})

@login_required(login_url="/login/")
@superuser_required
def delete_firmware(request):
    if request.method == 'POST':
        firmware_ids = request.POST.get('firmware_ids')
        if firmware_ids:
            firmware_ids_list = firmware_ids.split(',')
            firmwares_to_delete = Firmware.objects.filter(id__in=firmware_ids_list)
            deleted_count, _ = firmwares_to_delete.delete()
            logger.info(f"Successfully deleted {deleted_count} firmware(s).")
        else:
            logger.error("No firmware selected for deletion.")
    return redirect('firmware_list')


@login_required
@superuser_required
def update_device_firmware(request):
    if request.method == 'POST':
        device_ids_str = request.POST.get('device_ids')
        if device_ids_str:
            device_ids = device_ids_str.split(',')
        else:
            device_ids = []

        action = request.POST.get('action')
        lte_firmware_id = request.POST.get('lte_firmware_id')
        dectnr_firmware_id = request.POST.get('dectnr_firmware_id')

        devices = Device.objects.filter(id__in=device_ids)

        lte_firmware = Firmware.objects.get(id=lte_firmware_id) if lte_firmware_id else None
        dectnr_firmware = Firmware.objects.get(id=dectnr_firmware_id) if dectnr_firmware_id else None

        if action == 'reboot':
            if devices.count() == 1:
                device = devices.first()
                send_reboot_command(device)
            else:
                return redirect('device')
        elif action == 'update_firmware':
            send_firmware_update_command(request, devices, lte_firmware, dectnr_firmware)
            for device in devices:
                if lte_firmware:
                    device.lte_firmware_version = lte_firmware.version
                if dectnr_firmware:
                    device.dectnr_firmware_version = dectnr_firmware.version
                device.save()

        return redirect('device')
    else:
        return redirect('device')


def send_firmware_update_command(request, devices, lte_firmware=None, dectnr_firmware=None):
    cse_url = settings.ONE_M2M_CSE_URL
    originator = "CAdmin"
    request_identifier = generate_request_identifier()
    headers = {
        "X-M2M-Origin": originator,
        "X-M2M-RI": request_identifier,
        "X-M2M-RVI": "3",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    commands = []

    for device in devices:
        nod_rn = f"nod_{device.hardware_id}"
        if lte_firmware:
            resource_url = f"{cse_url}/{nod_rn}/flexNode/dmFirmware_lte/updateFirmware"
            lbl = ["LTE"]
            data = {
                "mad:updFe": {
                    "url": lte_firmware.get_download_url(request),
                    "versn": lte_firmware.version,
                    "resut": "Command Sent",
                    "lbl": lbl
                }
            }
            commands.append((resource_url, data, device))
        if dectnr_firmware:
            resource_url = f"{cse_url}/{nod_rn}/flexNode/dmFirmware_dectnr/updateFirmware"
            lbl = ["DECT_NR"]
            data = {
                "mad:updFe": {
                    "url": dectnr_firmware.get_download_url(request),
                    "versn": dectnr_firmware.version,
                    "resut": "Command Sent",
                    "lbl": lbl
                }
            }
            commands.append((resource_url, data, device))

    # Send Commands
    if commands:
        success = True
        for resource_url, data, device in commands:
            try:
                response = requests.put(resource_url, headers=headers, json=data, timeout=10)
                if response.status_code in [200, 201]:
                    logger.info(f"Firmware updated for device '{device.hardware_id}' using {resource_url}: {data}")
                else:
                    logger.error(f"Failed to update firmware for device '{device.hardware_id}' using {resource_url}: {response.status_code}, {response.text}")
                    success = False
            except requests.exceptions.RequestException as e:
                logger.error(f"Exception during firmware update for device '{device.hardware_id}' using {resource_url}: {e}")
                success = False
        return success
    else:
        logger.warning("No firmware provided for update command")
        return False


def send_reboot_command(device):
    # Reboot notification to PCU
    cse_url = settings.ONE_M2M_CSE_URL
    nod_rn = f"nod_{device.hardware_id}"
    command_url = f"{cse_url}/{nod_rn}/flexNode/dmAgent/reboot"
    status_url = f"{cse_url}/{nod_rn}/flexNode/dmAgent"
    originator = "CAdmin"
    request_identifier = generate_request_identifier()

    headers = {
        "X-M2M-Origin": originator,
        "X-M2M-RI": request_identifier,
        "X-M2M-RVI": "3",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    command_data = {
        "mad:rebot": {
            "rebTe": 1
        }
    }

    status_data = {
        "mad:dmAgt": {
            "state": 4
        }
    }

    try:
        response = requests.put(command_url, headers=headers,
                                json=command_data, timeout=10)
        if response.status_code in [200, 201]:
            logger.info(f"Reboot command sent to device '{device.hardware_id}'")
        else:
            logger.error(
                f"Failed to send reboot command to '{device.hardware_id}': {response.status_code}, {response.text}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Exception during reboot command for '{device.hardware_id}': {e}")
        return False

    try:
        response = requests.put(status_url, headers=headers, json=status_data,
                                timeout=10)
        if response.status_code in [200, 201]:
            logger.info(f"Reboot status updated for device '{device.hardware_id}'")
        else:
            logger.error(
                f"Failed to update reboot status for '{device.hardware_id}': {response.status_code}, {response.text}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Exception during reboot status update for '{device.hardware_id}': {e}")
        return False

    try:
        device.status = 'rebooting'
        device.save()
        logger.info(f"Device '{device.hardware_id}' status updated to 'rebooting'")
    except Exception as e:
        logger.error(f"Failed to update device status for '{device.hardware_id}': {e}")
        return False

    return True