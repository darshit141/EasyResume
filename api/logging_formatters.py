"""A minimal JSON log formatter — no external dependency (python-json-logger
et al.) for something this small. One JSON object per line on stdout,
which is what any real log aggregator (CloudWatch, Datadog, Loki, a
managed host's own log viewer) expects to ingest.
"""

import json
import logging


class JSONLogFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)
