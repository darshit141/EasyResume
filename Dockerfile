FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./
RUN mkdir -p /app/media && chown -R app:app /app

USER app

EXPOSE 8000

# Gunicorn no longer blocks on LLM calls — every AI-backed endpoint enqueues
# a Celery job and returns immediately (see config/celery.py, ai_jobs/) — so
# this timeout only needs to cover ordinary request/response work. Run a
# separate `celery -A config worker` process (same image) to actually
# execute queued AI jobs; see docker-compose.yml's `worker` service.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "60", "config.wsgi:application"]
