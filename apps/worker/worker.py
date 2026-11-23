"""Queue runtime for the ERP test deployment."""

import os

from celery import Celery

app = Celery("yihe_erp", broker=os.environ["CELERY_BROKER_URL"])
app.conf.update(
    task_default_queue="default",
    task_serializer="json",
    accept_content=["json"],
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
)
