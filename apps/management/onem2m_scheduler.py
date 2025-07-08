import os
import sys
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from apps.management.onem2m_service import job_register_web

def start_scheduler():

    # make sure only run scheduler on Main Env
    # (Django have autoreload with runserver, will result two process,
    # one is main, other is sub process, which cause scheduler to call twice)
    # When RUN_MAIN == true, means we are on sub process, which we don't want call another schedule
    if 'runserver' not in sys.argv:
        return

    if os.environ.get('RUN_MAIN') == 'true':
        return

    scheduler = BackgroundScheduler()

    scheduler.add_job(job_register_web, 'interval', minutes=10, id="webapp_ae_registration", next_run_time=datetime.now())

    scheduler.start()
