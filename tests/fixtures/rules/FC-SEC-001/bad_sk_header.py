"""FC-SEC-001 bad: an sk- key literal inside an Authorization header."""

# INTENTIONALLY FLAWED FIXTURE: the key below is fake but hard-coded on purpose.
HEADERS = {"Authorization": "Bearer sk-FAKE0000000000000000000000"}
