# Ensures the Celery app is loaded whenever Django starts, so shared_task
# decorators register against it (both under gunicorn and under `celery -A
# config worker`).
from config.celery import app as celery_app

__all__ = ("celery_app",)
