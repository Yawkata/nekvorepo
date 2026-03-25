"""
Shared structlog configuration for all microservices.
Call configure_logging(service_name) once at application startup.
"""
import logging
import structlog


def configure_logging(service_name: str, level: int = logging.INFO) -> None:
    """
    Configure structlog to emit newline-delimited JSON on every log record.
    The service_name is bound to every log entry so log aggregators can filter
    by service without needing a separate log stream per container.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
    # Bind the service name globally so it appears on every log line
    structlog.contextvars.bind_contextvars(service=service_name)
