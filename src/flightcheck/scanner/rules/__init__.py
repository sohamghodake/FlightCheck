"""Built-in rules. Importing this package registers them with ``scanner.engine``.

Registry order: BharatCode contract rules, robustness rules, security rules. Rule IDs are
permanent; a retired rule's ID is never reused (ADR-012).
"""

from flightcheck.scanner.rules import bharatcode, robustness, security

__all__ = ["bharatcode", "robustness", "security"]
