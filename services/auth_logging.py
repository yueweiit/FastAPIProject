"""Prevent OAuth query credentials from entering Uvicorn access logs."""

import logging


class OAuthAccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            target = record.args[2]
            if isinstance(target, str) and target.split("?", 1)[0].startswith("/auth/eims"):
                arguments = list(record.args)
                arguments[2] = target.split("?", 1)[0]
                record.args = tuple(arguments)
        return True


def protect_oauth_access_logs() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, OAuthAccessLogFilter) for item in logger.filters):
        logger.addFilter(OAuthAccessLogFilter())
