import logging
import threading
import time
import uuid
import requests

from constant.mapping import get_module_mapping
from core import settings

logger = logging.getLogger(__name__)

def generate_request_identifier():
    return str(uuid.uuid4())

def register_web():

    cse_url = settings.ONE_M2M_CSE_URL
    request_identifier = generate_request_identifier()

    headers = {
        "X-M2M-Origin": settings.ONE_M2M_ORIGINATOR,
        "X-M2M-RI": request_identifier,
        "X-M2M-RVI": "3",
        "Content-Type": "application/json;ty=2",
        "Accept": "application/json",
    }

    data = {
        "m2m:ae": {
            "rn": settings.ONE_M2M_AE_NAME,
            "api": settings.ONE_M2M_AE_API,
            "rr": True,
            "srv": settings.ONE_M2M_AE_SRVS,
        }
    }

    try:
        response = requests.post(cse_url, headers=headers, json=data, timeout=10)
        if response.status_code in [200, 201]:
            logger.info("WebApp successfully registered")
            return True
        else:
            logger.error(f"WebApp failed to register: {response.status_code}, {response.text}")
            return False

    except requests.exceptions.RequestException as e:
        logger.error(f"Exception occur during WebApp AE registration{e}")
        return False

def check_register_web():
    request_identifier = generate_request_identifier()
    web_url = f"{settings.ONE_M2M_CSE_URL}/{settings.ONE_M2M_AE_NAME}"

    headers = {
        "X-M2M-Origin": settings.ONE_M2M_ORIGINATOR,
        "X-M2M-RI": request_identifier,
        "X-M2M-RVI": "3",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        response = requests.get(web_url, headers=headers, timeout=10)
        if response.status_code == 200:
            logger.info("WebApp AE is already registered")
            return True
        else:
            logger.warning(f"WebApp AE is not registered: {response.status_code}, {response.text}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Exception occur during retrieving WebApp AE registration info: {e}")
        return False

def job_register_web():
    if not check_register_web():
        register_success = register_web()
        if not register_success:
            logger.error("job_register_web: WebApp AE registration failed")
        else:
            logger.info("job_register_web: WebApp AE registration successful")
    else:
        logger.info("job_register_web: WebApp AE registration already registered")

def register_device_ae(device):

    cse_url = settings.ONE_M2M_CSE_URL
    request_identifier = generate_request_identifier()

    # Create Node and get its RI
    node_ri = create_node(cse_url, device.hardware_id, "CAdmin")
    if not node_ri:
        logger.error("Failed to create node, AE registration aborted.")
        return False, None, None

    device_name_no_spaces = "".join(device.name.split())
    originator = f"C{device.hardware_id}"
    ae_rn = device.ae_rn
    ae_api = f"N{device.ae_rn}"

    headers = {
        "X-M2M-Origin": originator,
        "X-M2M-RI": request_identifier,
        "X-M2M-RVI": "3",
        "Content-Type": "application/json;ty=2",
        "Accept": "application/json",
    }

    data = {
        "m2m:ae": {
            "rn": ae_rn,
            "api": ae_api,
            "rr": False,
            "srv": settings.ONE_M2M_AE_SRVS,
            "nl": node_ri,
        }
    }

    try:
        # Step 1: Register Device as AE
        response = requests.post(cse_url, headers=headers, json=data, timeout=10)
        logger.debug(f"Received response: {response.status_code}, {response.text}")
        if response.status_code in [200, 201, 403, 409]:
            if response.status_code in [403, 409]:
                logger.warning(f"Device AE already exists: {response.status_code}, {response.text}")
            else:
                logger.info("Device AE successfully registered")

                if create_polling_channel(cse_url, ae_rn, originator):
                    logger.info(f"Polling Channel created for AE '{ae_rn}'")
                else:
                    logger.error(f"Failed to create Polling Channel for AE '{ae_rn}'")

                # Step 2: Create Module Resource for AE
                modules = ['lock', 'bikeData', "meshConnectivity", "battery"]
                rs_url = f"{ae_rn}"
                for module in modules:
                    if create_module(cse_url, rs_url, module, originator):
                        logger.info(f"Module '{module}' created for AE '{ae_rn}'")

                        # Step 3: Create Subscription for Module
                        sub_url = f"{cse_url}/{ae_rn}/{module}"
                        sub_rn = f"sub_{module}"
                        if create_subscription(sub_url, sub_rn, originator, settings.ONE_M2M_NOTIFICATIONS_URL):
                            logger.info(f"Subscription created for '{module}' of AE '{ae_rn}'")
                        else:
                            logger.error(f"Failed to create subscription for '{module}' of AE '{ae_rn}'")
                    else:
                        logger.error(f"Failed to create module '{module}' for AE '{ae_rn}'")

                # Step 4: Create Flexnode for Device management
                node_rn = f"nod_{device.hardware_id}"
                if create_flexnode(cse_url, node_rn, "CAdmin"):
                    logger.info(f"Parent module class 'flexNode' created for AE '{ae_rn}'")
                else:
                    logger.error(f"Failed to create parent module class 'flexNode' for AE '{ae_rn}'")

                # Step 5: Create Module for Device Management
                modules = ['dmAgent', 'dmFirmware_lte', 'dmFirmware_dectnr']
                rs_url = f"{node_rn}/flexNode"
                for module in modules:
                    if create_module(cse_url, rs_url, module, "CAdmin"):
                        logger.info(f"Module '{module}' created for Device Node '{node_rn}'")
                    else:
                        logger.error(f"Failed to create module '{module}' for Device Node '{node_rn}'")

                # Step 6: Create Resource for dmAgent
                ae_ri = f"C{device.hardware_id}"
                module = 'reboot'
                rs_url = f"{node_rn}/flexNode/dmAgent"
                if create_module(cse_url, rs_url, module, "CAdmin"):
                    logger.info(f"Action '{module}' created for Device Node '{node_rn}'")

                    sub_url = f"{cse_url}/{rs_url}/{module}"
                    sub_rn = f"sub_{module}"

                    if create_subscription_with_pch_verification(sub_url, sub_rn, "CAdmin", ae_ri, cse_url, ae_rn):
                        logger.info(f"Subscription created for '{module}' of Node '{node_rn}'")
                    else:
                        logger.error(f"Failed to create subscription for '{module}' of Node '{node_rn}'")
                else:
                    logger.error(f"Failed to create Action '{module}' for Device Node '{node_rn}'")

                # Step 7: Create Resource for dmFirmware
                parents = ['dmFirmware_lte', 'dmFirmware_dectnr']
                module = 'updateFirmware'
                for parent in parents:
                    rs_url = f"{node_rn}/flexNode/{parent}"
                    if create_module(cse_url, rs_url, module, "CAdmin"):
                        logger.info(f"Action '{module}' created for Device Resource '{parent}'")

                        sub_url = f"{cse_url}/{rs_url}/{module}"
                        sub_rn = f"sub_{module}"

                        if create_subscription_with_pch_verification(sub_url, sub_rn, "CAdmin", ae_ri, cse_url, ae_rn):
                            logger.info(f"Subscription created for '{module}' of Device Resource '{parent}'")
                        else:
                            logger.error(f"Failed to create subscription for '{module}' of Device Resource '{parent}'")
                    else:
                        logger.error(f"Failed to create Action '{module}' for Device Resource '{parent}'")

            return True, ae_rn, originator

        else:
            logger.error(f"Device AE failed to register: {response.status_code}, {response.text}")
            return False, None, None

    except requests.exceptions.RequestException as e:
        logger.error(f"Exception occur during Device AE registration: {e}")
        return False, None, None

def delete_device_ae(device):

    cse_url = settings.ONE_M2M_CSE_URL
    request_identifier = generate_request_identifier()
    ae_rn = device.ae_rn
    ae_url = cse_url + f"/{ae_rn}"
    nod_rn = f"nod_{device.hardware_id}"
    nod_url = cse_url + f"/{nod_rn}"

    headers = {
        "X-M2M-Origin": "CAdmin",
        "X-M2M-RI": request_identifier,
        "X-M2M-RVI": "3",
    }

    data = {}

    try:
        response = requests.delete(ae_url, headers=headers, json=data, timeout=10)
        if response.status_code in [200, 202, 204]:
            logger.info(f"Device AE {ae_rn} successfully deleted")
            response = requests.delete(nod_url, headers=headers, json=data, timeout=10)
            if response.status_code in [200, 202, 204]:
                logger.info(f"Device NOD {nod_rn} successfully deleted")
                return True
            else:
                logger.error(f"Device NOD {nod_rn} failed to delete: {response.status_code}, {response.text}")
                return False
        else:
            logger.error(f"Device AE {ae_rn} failed to delete: {response.status_code}, {response.text}")
            return False

    except requests.exceptions.RequestException as e:
        logger.error(f"Exception occur during AE deletion{e}")
        return False

def create_polling_channel(cse_url, ae_rn, originator):

    channel_url = f"{cse_url}/{ae_rn}"
    request_identifier = generate_request_identifier()

    headers = {
        "X-M2M-Origin": originator,
        "X-M2M-RI": request_identifier,
        "X-M2M-RVI": "3",
        "Content-Type": "application/json;ty=15",
        "Accept": "application/json",
    }

    data = {
        "m2m:pch": {
            "rn": "pch",
            'rqag': False,
        }
    }

    try:
        response = requests.post(channel_url, headers=headers, json=data, timeout=10)
        logger.debug(f"Received response for polling channel creation: {response.status_code}, {response.text}")
        if response.status_code in [200, 201, 403, 409]:
            if response.status_code in [403, 409]:
                logger.warning(f"Polling Channel already exists: {response.status_code}, {response.text}")
            else:
                logger.info(f"Polling Channel successfully created")
            return True
        else:
            logger.error(f"Polling Channel failed to create: {response.status_code}, {response.text}")
            return False

    except requests.exceptions.RequestException as e:
        logger.error(f"Exception occurred during polling channel creation: {e}")
        return False

def create_subscription_with_pch_verification(sub_url, sub_rn, originator, ae_originator, cse_url, ae_rn):
    request_identifier = generate_request_identifier()

    # Start a thread to poll the PCU and handle the verification
    verification_thread = threading.Thread(
        target=poll_pcu_and_verify_subscription,
        args=(cse_url, ae_rn, ae_originator)
    )
    verification_thread.start()

    # Wait a moment to ensure that the polling is ready
    time.sleep(0.5)

    # Create the subscription
    headers = {
        "X-M2M-Origin": originator,  # 'CAdmin' or similar
        "X-M2M-RI": request_identifier,
        "X-M2M-RVI": "3",
        "Content-Type": "application/json;ty=23",  # ty=23 Subscription
        "Accept": "application/json",
    }

    data = {
        "m2m:sub": {
            "rn": sub_rn,
            "nu": [ae_originator],  # Set to AE's originator
            "nct": 2,
            "enc": {
                "net": [1],
            },
            "su": ae_originator  # Subscriber URI for verification
        }
    }

    try:
        response = requests.post(sub_url, headers=headers, json=data, timeout=10)
        logger.debug(f"Subscription response: {response.status_code}, {response.text}")
        if response.status_code in [200, 201, 202]:
            logger.info(f"Subscription '{sub_url}' created successfully")
            # Wait for the verification to complete
            verification_thread.join()
            return True
        else:
            logger.error(f"Failed to create subscription '{sub_url}': {response.status_code}, {response.text}")
            verification_thread.join()
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Exception occurred during subscription creation: {e}")
        verification_thread.join()
        return False

def poll_pcu_and_verify_subscription(cse_url, ae_rn, ae_originator, timeout=10):
    pcu_url = f"{cse_url}/{ae_rn}/pch/pcu"
    headers = {
        "X-M2M-Origin": ae_originator,
        "X-M2M-RI": generate_request_identifier(),
        "X-M2M-RVI": "3",
        "Accept": "application/json",
    }

    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(pcu_url, headers=headers, timeout=10)
            if response.status_code == 200:
                notification = response.json()
                # Process the notification
                rqp = notification.get('m2m:rqp', {})
                rqi = rqp.get('rqi')
                if rqi:
                    # Send back the verification response
                    headers = {
                        "X-M2M-Origin": ae_originator,
                        "X-M2M-RI": generate_request_identifier(),
                        "X-M2M-RVI": "3",
                        "Accept": "application/json",
                    }
                    data = {
                        'm2m:rsp': {
                            'fr': ae_originator,
                            'rqi': rqi,
                            'rvi': '3',
                            'rsc': 2000  # RC_OK
                        }
                    }
                    response = requests.post(pcu_url, headers=headers, json=data, timeout=10)
                    if response.status_code == 200:
                        logger.info(f"Verification response sent successfully for rqi: {rqi}")
                        return
                    else:
                        logger.error(f"Failed to send verification response: {response.status_code}, {response.text}")
                        return
                else:
                    logger.error("No 'rqi' found in notification")
                    return
            elif response.status_code == 204:
                # No content, continue polling
                time.sleep(1)
            else:
                logger.error(f"Failed to retrieve notification from PCU: {response.status_code}, {response.text}")
                time.sleep(1)
        except requests.exceptions.RequestException as e:
            logger.error(f"Exception occurred during polling PCU: {e}")
            time.sleep(1)

    logger.error("Timeout while polling PCU for verification request")

def create_subscription(sub_url, sub_rn, originator, notification_url):

    request_identifier = generate_request_identifier()

    headers = {
        "X-M2M-Origin": originator,
        "X-M2M-RI": request_identifier,
        "X-M2M-RVI": "3",
        "Content-Type": "application/json;ty=23",  # ty=23 Subscription
        "Accept": "application/json",
    }

    data = {
        "m2m:sub": {
            "rn": sub_rn,
            "nu": [notification_url],
            "nct": 1,
            "enc": {
                "net": [1],
            },
        }
    }

    try:
        response = requests.post(sub_url, headers=headers, json=data)
        logger.debug(f"Subscription response: {response.status_code}, {response.text}")
        if response.status_code in [200, 201, 202]:
            logger.info(f"Subscription '{sub_url}' created successfully")
            return True
        else:
            logger.error(f"Failed to create subscription '{sub_url}': {response.status_code}, {response.text}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Exception occur during subscription creation: {e}")
        return False


def create_node(cse_url, hardware_id, originator):
    request_identifier = generate_request_identifier()

    headers = {
        "X-M2M-Origin": originator,
        "X-M2M-RI": request_identifier,
        "X-M2M-RVI": "3",
        "Content-Type": "application/json;ty=14",  # ty=14 for node
        "Accept": "application/json",
    }

    data = {
        "m2m:nod": {
            "ni": hardware_id,
            "rn": f"nod_{hardware_id}"
        }
    }

    try:
        response = requests.post(cse_url, headers=headers, json=data, timeout=10)
        logger.debug(f"Node creation response: {response.status_code}, {response.text}")
        if response.status_code in [200, 201]:
            node_info = response.json().get("m2m:nod")
            node_ri = node_info.get("ri")
            logger.info(f"Node successfully created with RI: {node_ri}")
            return node_ri
        else:
            logger.error(f"Failed to create node: {response.status_code}, {response.text}")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Exception occurred during node creation: {e}")
        return None

def create_flexnode(cse_url, node_rn, originator):

    flexnode_url = f"{cse_url}/{node_rn}"
    request_identifier = generate_request_identifier()

    headers = {
        "X-M2M-Origin": originator,
        "X-M2M-RI": request_identifier,
        "X-M2M-RVI": "3",
        "Content-Type": "application/json;ty=28",  # ty=28 for flexContainer
        "Accept": "application/json",
    }

    data = {
        "mad:fleNe": {
            "rn": "flexNode",  # flexNode as the root container/module
            "lbl": ["flexNode"],
            "cnd": "org.onem2m.management.device.flexNode"
        }
    }

    try:
        response = requests.post(flexnode_url, headers=headers, json=data, timeout=10)
        logger.debug(f"Received response for flexNode creation: {response.status_code}, {response.text}")
        if response.status_code in [200, 201, 403, 409]:
            if response.status_code in [403, 409]:
                logger.warning(f"flexNode already exists: {response.status_code}, {response.text}")
            else:
                logger.info("flexNode successfully created")
            return True
        else:
            logger.error(f"flexNode failed to create: {response.status_code}, {response.text}")
            return False

    except requests.exceptions.RequestException as e:
        logger.error(f"Exception occurred during flexNode creation: {e}")
        return False


def create_module(cse_url, rs_url, module_rn, originator):
    module_url = f"{cse_url}/{rs_url}"
    request_identifier = generate_request_identifier()
    module_info = get_module_mapping(module_rn)

    mapped_module_name = module_info["short_name"]
    default_attributes = module_info.get("attributes", {})
    cnd_type = module_info["cnd_type"]
    domain = module_info["domain"]
    type_ = module_info["type"]
    full_name = module_info["full_name"]

    cnd = f"org.{domain}.{cnd_type}.{type_}.{full_name}"

    headers = {
        "X-M2M-Origin": originator,
        "X-M2M-RI": request_identifier,
        "X-M2M-RVI": "3",
        "Content-Type": "application/json;ty=28", # ty=28 flexContainer module
        "Accept": "application/json",
    }

    data = {
        f"{mapped_module_name}": {
            "rn": module_rn,
            "cnd": cnd,
        }
    }

    # Extra attributes serve for customize attributes like "lock" in "cod:lock"
    data[mapped_module_name].update(default_attributes)

    try:
        response = requests.post(module_url, headers=headers, json=data, timeout=10)
        logger.debug(f"Module creation response: {response.status_code}, {response.text}")

        if response.status_code in [200, 201]:
            logger.info(f"Module '{module_rn}' successfully created.")
            return True
        elif response.status_code in [403, 409]:
            logger.warning(f"Module '{module_rn}' already exists or conflict: {response.status_code}, {response.text}")
            return True
        else:
            logger.error(f"Failed to create module '{module_rn}': {response.status_code}, {response.text}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Exception during module creation: {e}")
        return False

def update_lock_module(ae_rn, originator, status):
    cse_url = settings.ONE_M2M_CSE_URL
    lock_resource_url = f"{cse_url}/{ae_rn}/lock"
    request_identifier = generate_request_identifier()

    headers = {
        "X-M2M-Origin": originator,
        "X-M2M-RI": request_identifier,
        "X-M2M-RVI": "3",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    data = {
        "cod:lock": {
            "lock": status
        }
    }

    try:
        response = requests.put(lock_resource_url, headers=headers, json=data, timeout=10)
        logger.debug(f"Lock update response: {response.status_code}, {response.text}")

        if response.status_code in [200, 201]:
            logger.info(f"Lock status successfully updated to '{status}' for AE '{ae_rn}'")
            return True
        else:
            logger.error(f"Failed to update lock status: {response.status_code}, {response.text}")
            return False

    except requests.exceptions.RequestException as e:
        logger.error(f"Exception occurred during lock status update: {e}")
        return False