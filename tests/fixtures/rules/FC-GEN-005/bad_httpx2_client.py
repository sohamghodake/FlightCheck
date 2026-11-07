"""FC-GEN-005 bad: a raw HTTPX2 client without an explicit timeout."""

# INTENTIONALLY FLAWED FIXTURE: the client has no deliberate timeout.
import os

import httpx2

http = httpx2.Client(base_url=os.environ["BHARATCODE_BASE_URL"])
