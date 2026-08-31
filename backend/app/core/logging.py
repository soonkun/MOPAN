import json
import logging
import sys
from contextvars import ContextVar
from typing import Any

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        payload.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(environment: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if environment == "development":
        handler.setFormatter(logging.Formatter("%(levelname)-5.5s [%(name)s] %(message)s"))
    else:
        handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if environment == "development" else logging.INFO)
    # pdfminer logs one DEBUG record per token it lexes out of the PDF. On a
    # development root logger that is not noise, it is the job: MEASURED on
    # 유사상품 심사기준, 15 pages emitted 2,025,350 records and took 48.9s at
    # DEBUG against 7.5s at WARNING - 6.5x, or 55 minutes against 8 for the
    # 1011-page document, before the log driver's own cost. Pinned here rather
    # than in the parser because the parser is not the only importer, and because
    # a level set inside a library call would be undone by the next
    # configure_logging.
    for noisy in ("pdfminer", "pdfplumber"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(logger: logging.Logger, message: str, /, **fields: Any) -> None:
    """Structured info log. Slice 5's dashboard reads these fields; do not
    inline values into the message string."""
    logger.info(message, extra={"extra_fields": fields})
