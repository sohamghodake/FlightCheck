"""FC-SEC-002 bad: the API key (or a prefix of it) is printed and logged."""

# INTENTIONALLY FLAWED FIXTURE: credentials are printed on purpose.
import logging
import os

logger = logging.getLogger(__name__)


def show_config():
    api_key = os.environ["BHARATCODE_API_KEY"]
    print(f"Using key {api_key[:4]}...")
    logger.info("auth header: %s", os.environ.get("BHARATCODE_API_KEY"))
