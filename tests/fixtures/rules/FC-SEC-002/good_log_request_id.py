"""FC-SEC-002 good: only the request ID and token usage are logged."""

import logging

logger = logging.getLogger(__name__)


def log_response(response):
    logger.info("request %s", response._request_id)
    print("tokens used:", response.usage.total_tokens)
