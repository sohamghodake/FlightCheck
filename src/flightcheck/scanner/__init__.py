"""Static analysis: discovery → AST index → provider detection (→ rules, from Phase 3).

This package reads repository source with ``ast.parse`` only. It never imports, executes or
compiles repository code, and it must not import ``providers``, ``runtime``, ``subprocess``,
``importlib`` or ``runpy`` (docs/ARCHITECTURE.md §3, plan §20.6).
"""
