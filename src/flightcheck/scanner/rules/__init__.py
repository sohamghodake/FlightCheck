"""Built-in rules. Importing this package registers them with ``scanner.engine``.

Rule IDs are permanent; a retired rule's ID is never reused (ADR-012).
"""

from flightcheck.scanner.rules import bharatcode

__all__ = ["bharatcode"]
