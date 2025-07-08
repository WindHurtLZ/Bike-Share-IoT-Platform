from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Device
from .onem2m_service import register_device_ae
import logging
import os

logger = logging.getLogger(__name__)

@receiver(post_save, sender=Device)
def create_device_ae(sender, instance, created, **kwargs):

    if not os.environ.get('RUN_MAIN', False):
        return

    if created:
        success, rn, originator = register_device_ae(instance)
        if success:
            logger.info(f"Device AE registered: RN={rn}, Originator={originator}")
        else:
            logger.error(f"Failed to register Device AE for device: {instance.name}")
