"""AST index: one ``NodeVisitor`` pass per file (plan §16.1 step 3).

Source is read as bytes and parsed with ``ast.parse`` only; nothing from the repository is
imported or executed. Receivers are resolved one hop (ADR-011): a name bound in the current
function (or an enclosing function), then the module; ``self.<attr>`` bound in the same class;
or an inline constructor call. Anything else keeps its call site with ``binding=None``.
"""

from __future__ import annotations

import ast
import io
import os
import tokenize
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from flightcheck.core.models import Diagnostic, Location
from flightcheck.scanner.ast_utils import (
    Unknown,
    attr_chain,
    literal_value,
    source_segment,
    split_attrs,
    string_fragments,
)
from flightcheck.scanner.discovery import DiscoveryOptions, DiscoveryResult, discover

CLIENT_CONSTRUCTORS = frozenset(
    {
        "openai.OpenAI",
        "openai.AsyncOpenAI",
        "httpx2.Client",
        "httpx2.AsyncClient",
        "httpx.Client",
        "httpx.AsyncClient",
        "requests.Session",
    }
)
SDK_CONSTRUCTORS = frozenset({"openai.OpenAI", "openai.AsyncOpenAI"})
HTTP_MODULES = frozenset({"httpx", "httpx2", "requests"})
CLIENT_KWARGS = ("base_url", "api_key", "timeout", "max_retries")

CallKind = Literal[
    "responses.create",
    "responses.stream",
    "responses.parse",
    "chat.completions.create",
    "chat.completions.parse",
    "chat.completions.stream",
    "http.post",
]
SDK_CALL_SUFFIXES: dict[tuple[str, ...], CallKind] = {
    ("responses", "create"): "responses.create",
    ("responses", "stream"): "responses.stream",
    ("responses", "parse"): "responses.parse",
    ("chat", "completions", "create"): "chat.completions.create",
    ("chat", "completions", "parse"): "chat.completions.parse",
    ("chat", "completions", "stream"): "chat.completions.stream",
}
RAW_URL_MARKERS = {"/responses": "responses", "/chat/completions": "chat.completions"}

Resolution = Literal["function", "module", "class_attr", "inline"]
MODULE_SCOPE = "<module>"


# --- Index data -----------------------------------------------------------------------------


@dataclass
class ClientBinding:
    id: str  # "<path>:<line>:<col>" of the constructor call
    constructor: str  # fully qualified, e.g. "openai.OpenAI"
    location: Location
    kwargs: dict[str, Any]  # base_url/api_key/timeout/max_retries: literal value or Unknown
    env_kwargs: dict[str, str]  # kwarg -> environment variable name it is read from
    base_url_fragments: list[str]  # constant string pieces of base_url (one-hop names)
    target: str | None  # "client" or "self.client"; None if not assigned to a name
    scope: str  # qualname of the binding scope ("<module>", "Agent", "Agent.__init__")
    class_name: str | None
    is_async: bool


@dataclass
class CallSite:
    id: str  # "<path>:<line>:<col>"
    kind: CallKind
    endpoint: Literal["responses", "chat.completions"]
    location: Location
    receiver: str  # receiver expression text (e.g. "self.client", "OpenAI(...)")
    binding: ClientBinding | None
    resolution: Resolution | None
    kwargs: dict[str, Any]  # literal value or Unknown, per keyword argument
    params: dict[str, Any]  # request parameters: SDK kwargs, or the json= dict for raw posts
    has_star_kwargs: bool  # ``**kwargs`` present: parameters may be set dynamically
    stream: bool | None  # None when ``stream=`` is not a literal
    url: str | None  # raw HTTP posts: constant URL fragments joined
    function: str | None  # enclosing function qualname
    class_name: str | None
    in_try: bool  # lexically inside a ``try`` body (handlers/finally excluded)
    try_handlers: list[str]  # exception names of enclosing handlers, innermost first
    decorators: list[str]  # decorators of the enclosing function
    is_async_function: bool
    # Parameters whose value is a bare name bound one hop (ADR-011) to a list/dict/tuple
    # literal, e.g. ``tools=TOOLS`` with ``TOOLS = [...]``: parameter -> that literal's value.
    # ``kwargs``/``params`` still hold ``Unknown`` for these names.
    one_hop: dict[str, Any] = field(default_factory=dict)


@dataclass
class EnvRead:
    name: str
    kind: Literal["environ_subscript", "environ_get", "getenv"]
    has_default: bool
    location: Location
    function: str | None


@dataclass
class FunctionFacts:
    qualname: str
    location: Location
    class_name: str | None
    is_async: bool
    decorators: list[str]
    attributes: set[str] = field(default_factory=set)
    strings: set[str] = field(default_factory=set)


@dataclass
class StringLiteral:
    value: str  # raw: never written to a report without redaction
    location: Location


@dataclass
class FileIndex:
    path: str  # POSIX, relative to the scan root
    parsed: bool = True
    imports: dict[str, str] = field(default_factory=dict)
    clients: list[ClientBinding] = field(default_factory=list)
    call_sites: list[CallSite] = field(default_factory=list)
    env_reads: list[EnvRead] = field(default_factory=list)
    functions: dict[str, FunctionFacts] = field(default_factory=dict)
    string_literals: list[StringLiteral] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    size_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly view for debugging (raw strings included: redact before printing)."""
        return _jsonable(self)

    def counts(self) -> dict[str, int]:
        return {
            "imports": len(self.imports),
            "clients": len(self.clients),
            "call_sites": len(self.call_sites),
            "env_reads": len(self.env_reads),
            "functions": len(self.functions),
            "string_literals": len(self.string_literals),
            "diagnostics": len(self.diagnostics),
        }


@dataclass
class RepoIndex:
    root: Path
    files: list[FileIndex] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)  # discovery + per-file
    total_bytes: int = 0

    @property
    def clients(self) -> list[ClientBinding]:
        return [c for f in self.files for c in f.clients]

    @property
    def call_sites(self) -> list[CallSite]:
        return [c for f in self.files for c in f.call_sites]

    @property
    def env_reads(self) -> list[EnvRead]:
        return [e for f in self.files for e in f.env_reads]

    def file(self, path: str) -> FileIndex | None:
        return next((f for f in self.files if f.path == path), None)

    def counts(self) -> dict[str, int]:
        totals = {"files": len(self.files), "parsed": sum(f.parsed for f in self.files)}
        for f in self.files:
            for key, value in f.counts().items():
                if key != "diagnostics":
                    totals[key] = totals.get(key, 0) + value
        totals["diagnostics"] = len(self.diagnostics)
        return totals


def _jsonable(value: Any) -> Any:
    if isinstance(value, Location):
        return value.model_dump()
    if isinstance(value, Diagnostic):
        return value.model_dump()
    if isinstance(value, Unknown):
        return {"unknown": value.source}
    if isinstance(value, CallSite):
        out = {f.name: _jsonable(getattr(value, f.name)) for f in fields(value)}
        out["binding"] = value.binding.id if value.binding else None
        return out
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, set | frozenset):
        return sorted(_jsonable(v) for v in value)
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, complex):
        return str(value)
    return value


# --- Visitor --------------------------------------------------------------------------------


@dataclass
class _Scope:
    kind: Literal["module", "class", "function"]
    name: str
    qualname: str
    names: dict[str, list[ClientBinding]] = field(default_factory=dict)
    attrs: dict[str, list[ClientBinding]] = field(default_factory=dict)  # class scope only
    str_consts: dict[str, str] = field(default_factory=dict)
    env_names: dict[str, str] = field(default_factory=dict)  # name -> env var it was read from
    containers: dict[str, Any] = field(default_factory=dict)  # name -> list/dict/tuple literal
    # Every name bound in this scope so far (any value, parameters, loop targets): a binding
    # here shadows outer scopes even when its value is not a constant FlightCheck can use.
    bound: set[str] = field(default_factory=set)
    self_name: str | None = None  # first parameter of a method
    facts: FunctionFacts | None = None
    try_stack: list[list[str]] = field(default_factory=list)


@dataclass
class _Pending:
    call: CallSite
    receiver_node: ast.AST
    scopes: list[_Scope]


def _loc(path: str, node: ast.AST) -> Location:
    return Location(
        path=path,
        line=max(getattr(node, "lineno", 0), 0),
        column=max(getattr(node, "col_offset", 0), 0),
        end_line=getattr(node, "end_lineno", None),
    )


def _node_id(path: str, node: ast.AST) -> str:
    return f"{path}:{getattr(node, 'lineno', 0)}:{getattr(node, 'col_offset', 0)}"


class _IndexVisitor(ast.NodeVisitor):
    def __init__(self, path: str, source: str) -> None:
        self.path = path
        self.source = source
        self.index = FileIndex(path=path)
        self.module = _Scope(kind="module", name=MODULE_SCOPE, qualname=MODULE_SCOPE)
        self.scopes: list[_Scope] = [self.module]
        self.pending: list[_Pending] = []
        self.inline: dict[int, ClientBinding] = {}  # id(ctor Call node) -> binding

    # -- helpers ---------------------------------------------------------------------------

    @property
    def scope(self) -> _Scope:
        return self.scopes[-1]

    def _function_scope(self) -> _Scope | None:
        for scope in reversed(self.scopes):
            if scope.kind == "function":
                return scope
        return None

    def _class_name(self) -> str | None:
        for scope in reversed(self.scopes):
            if scope.kind == "class":
                return scope.qualname
        return None

    def _enclosing_class_scope(self) -> _Scope | None:
        """The class whose method we are directly in (where ``self.<attr>`` is stored)."""
        for i in range(len(self.scopes) - 1, 0, -1):
            if self.scopes[i].kind == "function":
                parent = self.scopes[i - 1]
                return parent if parent.kind == "class" and self.scopes[i].self_name else None
        return None

    def _self_name(self) -> str | None:
        fn = self._function_scope()
        return fn.self_name if fn is not None else None

    def qualify(self, node: ast.AST) -> str | None:
        """Resolve a ``Name``/``Attribute`` chain through the import alias map."""
        chain = attr_chain(node)
        if chain is None:
            return None
        head = self.index.imports.get(chain[0])
        if head is None:
            return ".".join(chain)
        return ".".join([head, *chain[1:]])

    def _expr_name(self, node: ast.AST) -> str:
        target = node.func if isinstance(node, ast.Call) else node
        name = self.qualify(target)
        return name if name is not None else source_segment(self.source, node, 120)

    def _lookup(self, name: str, table: str) -> Any:
        """One-hop value of ``name`` from the nearest scope that binds it, else ``None``.

        ``table`` is ``"str_consts"``, ``"containers"`` or ``"env_names"``. The nearest binding
        wins even when its value is unusable (``x = make()``, a parameter), so an outer
        constant is never seen through a local rebinding.
        """
        for scope in reversed(self.scopes):
            if scope.kind == "class" and scope is not self.scope:
                continue
            values = getattr(scope, table)
            if name in values:
                return values[name]
            if name in scope.bound:
                return None
        return None

    def _resolve_str(self, name: str) -> str | None:
        return self._lookup(name, "str_consts")

    def _resolve_container(self, name: str) -> Any:
        """One-hop list/dict/tuple literal bound to ``name`` (same lookup as strings), or None."""
        return self._lookup(name, "containers")

    def _shadow(self, target: ast.AST) -> None:
        """Record every name stored by ``target`` as bound to an unknown value."""
        scope = self.scope
        for node in ast.walk(target):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                scope.bound.add(node.id)
                scope.str_consts.pop(node.id, None)
                scope.containers.pop(node.id, None)
                scope.env_names.pop(node.id, None)

    def _one_hop(self, name_node: ast.AST) -> Any:
        if isinstance(name_node, ast.Name):
            return self._resolve_container(name_node.id)
        return None

    def _env_name_of(self, node: ast.AST) -> str | None:
        """Env var name if ``node`` reads one (directly, via ``or``, or via a one-hop name)."""
        if isinstance(node, ast.BoolOp):
            for value in node.values:
                found = self._env_name_of(value)
                if found:
                    return found
            return None
        read = self._env_read(node)
        if read is not None:
            return read[0]
        if isinstance(node, ast.Name):
            return self._lookup(node.id, "env_names")
        return None

    def _env_read(self, node: ast.AST) -> tuple[str, str, bool] | None:
        """``(name, kind, has_default)`` for ``os.environ[...]``/``.get``/``os.getenv``."""
        if isinstance(node, ast.Subscript) and self.qualify(node.value) == "os.environ":
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                return key.value, "environ_subscript", False
            return None
        if isinstance(node, ast.Call):
            callee = self.qualify(node.func)
            if callee in ("os.environ.get", "os.getenv") and node.args:
                key = node.args[0]
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    kind = "environ_get" if callee == "os.environ.get" else "getenv"
                    has_default = len(node.args) > 1 or any(
                        kw.arg == "default" for kw in node.keywords
                    )
                    return key.value, kind, has_default
        return None

    def _handler_names(self, handler: ast.ExceptHandler) -> list[str]:
        if handler.type is None:
            return ["BaseException"]
        types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        return [self._expr_name(t) for t in types]

    def _kwargs(self, call: ast.Call) -> tuple[dict[str, Any], bool]:
        out: dict[str, Any] = {}
        star = False
        for kw in call.keywords:
            if kw.arg is None:
                star = True
                continue
            out[kw.arg] = literal_value(kw.value, self.source)
        return out, star

    # -- scopes ----------------------------------------------------------------------------

    def visit_Module(self, node: ast.Module) -> None:
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in [*node.bases, *node.keywords]:
            self.visit(base)
        qual = self._child_qualname(node.name)
        self.scopes.append(_Scope(kind="class", name=node.name, qualname=qual))
        for stmt in node.body:
            self.visit(stmt)
        self.scopes.pop()

    def _child_qualname(self, name: str) -> str:
        parent = self.scope
        if parent.kind == "module":
            return name
        if parent.kind == "function":
            return f"{parent.qualname}.<locals>.{name}"
        return f"{parent.qualname}.{name}"

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        parent = self.scope
        qual = self._child_qualname(node.name)
        self_name = None
        if parent.kind == "class":
            positional = [*node.args.posonlyargs, *node.args.args]
            is_static = any(self._expr_name(d) == "staticmethod" for d in node.decorator_list)
            if positional and not is_static:
                self_name = positional[0].arg
        decorators = [self._expr_name(d) for d in node.decorator_list]
        facts = FunctionFacts(
            qualname=qual,
            location=_loc(self.path, node),
            class_name=parent.qualname if parent.kind == "class" else self._class_name(),
            is_async=isinstance(node, ast.AsyncFunctionDef),
            decorators=decorators,
        )
        self.index.functions[qual] = facts
        scope = _Scope(kind="function", name=node.name, qualname=qual, facts=facts)
        scope.self_name = self_name
        args = node.args
        scope.bound.update(
            a.arg
            for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
            if a is not None
        )
        self.scopes.append(scope)
        for stmt in node.body:
            self.visit(stmt)
        self.scopes.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # Lambdas are not scopes for binding purposes; their bodies are still indexed.
        self.generic_visit(node)

    # -- try bodies ------------------------------------------------------------------------

    def _visit_try(self, node: ast.Try) -> None:
        names: list[str] = []
        for handler in node.handlers:
            names.extend(self._handler_names(handler))
        self.scope.try_stack.append(names)
        for stmt in node.body:
            self.visit(stmt)
        self.scope.try_stack.pop()
        for part in [*node.handlers, *node.orelse, *node.finalbody]:
            self.visit(part)

    visit_Try = _visit_try
    visit_TryStar = _visit_try

    # -- imports ---------------------------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname:
                self.index.imports[alias.asname] = alias.name
            else:
                head = alias.name.split(".")[0]
                self.index.imports[head] = head

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level or not node.module:
            for alias in node.names:
                if alias.name != "*":
                    prefix = "." * node.level + (node.module or "")
                    self.index.imports[alias.asname or alias.name] = f"{prefix}.{alias.name}"
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            self.index.imports[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    # -- assignments -----------------------------------------------------------------------

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self.visit(target)
            self._bind(target, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
        self.visit(node.target)
        if node.value is not None:
            self._bind(node.target, node.value)

    def visit_withitem(self, node: ast.withitem) -> None:
        self.visit(node.context_expr)
        if node.optional_vars is not None:
            self.visit(node.optional_vars)
            self._bind(node.optional_vars, node.context_expr)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.generic_visit(node)
        self._shadow(node.target)

    def _visit_for(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        self._shadow(node.target)
        self.visit(node.target)
        for stmt in [*node.body, *node.orelse]:
            self.visit(stmt)

    visit_For = _visit_for
    visit_AsyncFor = _visit_for

    def _bind(self, target: ast.AST, value: ast.AST) -> None:
        scope = self.scope
        if not isinstance(target, ast.Name):
            self._shadow(target)
        if isinstance(target, ast.Name):
            scope.bound.add(target.id)
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                scope.str_consts[target.id] = value.value
            else:
                scope.str_consts.pop(target.id, None)
            container = None
            if isinstance(value, ast.List | ast.Dict | ast.Tuple):
                container = literal_value(value, self.source)
            if container is not None and not isinstance(container, Unknown):
                scope.containers[target.id] = container
            else:
                scope.containers.pop(target.id, None)
            env = self._env_name_of(value)
            if env:
                scope.env_names[target.id] = env
            else:
                scope.env_names.pop(target.id, None)
        binding = self.inline.get(id(value)) if isinstance(value, ast.Call) else None
        if binding is None:
            return
        if isinstance(target, ast.Name):
            binding.target = target.id
            binding.scope = scope.qualname
            scope.names.setdefault(target.id, []).append(binding)
            return
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == self._self_name()
        ):
            cls = self._enclosing_class_scope()
            if cls is not None:
                binding.target = f"{target.value.id}.{target.attr}"
                binding.scope = scope.qualname
                cls.attrs.setdefault(target.attr, []).append(binding)

    # -- facts -----------------------------------------------------------------------------

    def _record_fact(self, attr: str | None = None, string: str | None = None) -> None:
        for scope in self.scopes:
            if scope.facts is None:
                continue
            if attr is not None:
                scope.facts.attributes.add(attr)
            if string is not None:
                scope.facts.strings.add(string)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._record_fact(attr=node.attr)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self._record_fact(string=node.value)
            self.index.string_literals.append(
                StringLiteral(value=node.value, location=_loc(self.path, node))
            )

    def visit_Subscript(self, node: ast.Subscript) -> None:
        read = self._env_read(node)
        if read is not None:
            self._add_env_read(node, read)
        self.generic_visit(node)

    def _add_env_read(self, node: ast.AST, read: tuple[str, str, bool]) -> None:
        fn = self._function_scope()
        self.index.env_reads.append(
            EnvRead(
                name=read[0],
                kind=read[1],  # type: ignore[arg-type]
                has_default=read[2],
                location=_loc(self.path, node),
                function=fn.qualname if fn else None,
            )
        )

    # -- calls -----------------------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        read = self._env_read(node)
        if read is not None:
            self._add_env_read(node, read)
        callee = self.qualify(node.func)
        if callee in CLIENT_CONSTRUCTORS:
            self._add_client(node, callee)
        if isinstance(node.func, ast.Attribute):
            self._maybe_call_site(node)
        self.generic_visit(node)

    def _add_client(self, node: ast.Call, constructor: str) -> None:
        kwargs: dict[str, Any] = {}
        env_kwargs: dict[str, str] = {}
        fragments: list[str] = []
        for kw in node.keywords:
            if kw.arg not in CLIENT_KWARGS:
                continue
            kwargs[kw.arg] = literal_value(kw.value, self.source)
            env = self._env_name_of(kw.value)
            if env:
                env_kwargs[kw.arg] = env
            if kw.arg == "base_url":
                fragments = string_fragments(kw.value, self._resolve_str)
        binding = ClientBinding(
            id=_node_id(self.path, node),
            constructor=constructor,
            location=_loc(self.path, node),
            kwargs=kwargs,
            env_kwargs=env_kwargs,
            base_url_fragments=fragments,
            target=None,
            scope=self.scope.qualname,
            class_name=self._class_name(),
            is_async="Async" in constructor,
        )
        self.inline[id(node)] = binding
        self.index.clients.append(binding)

    def _maybe_call_site(self, node: ast.Call) -> None:
        base, attrs = split_attrs(node.func)
        kind: CallKind | None = None
        receiver_attrs: list[str] = []
        for suffix, suffix_kind in SDK_CALL_SUFFIXES.items():
            if len(attrs) >= len(suffix) and tuple(attrs[-len(suffix) :]) == suffix:
                kind = suffix_kind
                receiver_attrs = attrs[: -len(suffix)]
                break
        url: str | None = None
        if kind is None and attrs and attrs[-1] == "post":
            url_node = node.args[0] if node.args else None
            for kw in node.keywords:
                if kw.arg == "url":
                    url_node = kw.value
            if url_node is None:
                return
            url = "".join(string_fragments(url_node, self._resolve_str))
            endpoint = next((ep for marker, ep in RAW_URL_MARKERS.items() if marker in url), None)
            if endpoint is None:
                return
            kind = "http.post"
            receiver_attrs = attrs[:-1]
        if kind is None:
            return
        endpoint_name = "responses" if kind.startswith("responses") else "chat.completions"
        if kind == "http.post":
            assert url is not None
            endpoint_name = "chat.completions" if "/chat/completions" in url else "responses"
        if receiver_attrs and receiver_attrs[-1] == "beta":
            receiver_attrs = receiver_attrs[:-1]  # client.beta.chat.completions.parse
        receiver_node: ast.AST = base
        for attr in receiver_attrs:
            receiver_node = ast.Attribute(value=receiver_node, attr=attr, ctx=ast.Load())
        kwargs, star = self._kwargs(node)
        params = kwargs
        one_hop: dict[str, Any] = {}
        for kw in node.keywords:
            resolved = self._one_hop(kw.value) if kw.arg is not None else None
            if resolved is not None:
                one_hop[kw.arg] = resolved
        if kind == "http.post":
            params = {}
            one_hop = {}
            json_node = next((kw.value for kw in node.keywords if kw.arg == "json"), None)
            if isinstance(json_node, ast.Dict):
                for key, value in zip(json_node.keys, json_node.values, strict=True):
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        params[key.value] = literal_value(value, self.source)
                        resolved = self._one_hop(value)
                        if resolved is not None:
                            one_hop[key.value] = resolved
                    elif key is None:
                        star = True
            elif json_node is not None:
                star = True
        stream_value = params.get("stream", False)
        if kind.endswith(".stream"):
            stream: bool | None = True
        elif isinstance(stream_value, Unknown):
            stream = None
        else:
            stream = bool(stream_value)
        fn = self._function_scope()
        # A try body protects calls in the same function only (a def inside a try is not
        # protected when it is later called); outside functions, module and class bodies count.
        owners = self.scopes[self.scopes.index(fn) :] if fn is not None else self.scopes
        handlers: list[str] = []
        for owner in reversed(owners):
            for names in reversed(owner.try_stack):
                handlers.extend(names)
        in_try = any(owner.try_stack for owner in owners)
        receiver_text = source_segment(self.source, base, 120)
        if receiver_attrs:
            receiver_text += "." + ".".join(receiver_attrs)
        call = CallSite(
            id=_node_id(self.path, node),
            kind=kind,
            endpoint=endpoint_name,  # type: ignore[arg-type]
            location=_loc(self.path, node),
            receiver=receiver_text,
            binding=None,
            resolution=None,
            kwargs=kwargs,
            params=params,
            has_star_kwargs=star,
            stream=stream,
            url=url,
            function=fn.qualname if fn else None,
            class_name=self._class_name(),
            in_try=in_try,
            try_handlers=list(dict.fromkeys(handlers)),
            decorators=list(fn.facts.decorators) if fn and fn.facts else [],
            is_async_function=bool(fn and fn.facts and fn.facts.is_async),
            one_hop=one_hop,
        )
        self.index.call_sites.append(call)
        self.pending.append(
            _Pending(call=call, receiver_node=receiver_node, scopes=list(self.scopes))
        )

    # -- resolution (after the whole file is visited, so later bindings are visible) ---------

    def resolve(self) -> None:
        for item in self.pending:
            binding, how = self._resolve_receiver(item)
            item.call.binding = binding
            item.call.resolution = how

    def _resolve_receiver(self, item: _Pending) -> tuple[ClientBinding | None, Resolution | None]:
        node = item.receiver_node
        line = item.call.location.line
        if isinstance(node, ast.Call):
            binding = self.inline.get(id(node))
            return (binding, "inline") if binding else (None, None)
        if isinstance(node, ast.Name):
            # current function, enclosing functions, then module (class bodies are skipped,
            # as in Python's own name resolution)
            innermost = item.scopes[-1]
            for scope in reversed(item.scopes):
                if scope.kind == "class" and scope is not innermost:
                    continue
                bindings = scope.names.get(node.id)
                if bindings:
                    how: Resolution = "module" if scope.kind == "module" else "function"
                    return _pick(bindings, line, same_scope=scope is innermost), how
            return None, None
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            fn = next((s for s in reversed(item.scopes) if s.kind == "function"), None)
            if fn is None or fn.self_name != node.value.id:
                return None, None
            idx = item.scopes.index(fn)
            if idx == 0 or item.scopes[idx - 1].kind != "class":
                return None, None
            bindings = item.scopes[idx - 1].attrs.get(node.attr)
            if not bindings:
                # class-body assignment ``client = OpenAI()`` reached as ``self.client``
                bindings = item.scopes[idx - 1].names.get(node.attr)
            if bindings:
                return bindings[-1], "class_attr"
        return None, None


def _pick(bindings: list[ClientBinding], line: int, *, same_scope: bool) -> ClientBinding:
    """Latest binding at or before ``line`` in the same scope; otherwise the last one."""
    if same_scope:
        before = [b for b in bindings if b.location.line <= line]
        if before:
            return before[-1]
    return bindings[-1]


# --- Entry points ---------------------------------------------------------------------------


def decode_source(data: bytes) -> tuple[str, bool]:
    """Decode Python source bytes. Returns ``(text, lossy)``.

    Honours a BOM and a PEP 263 coding cookie; anything undecodable falls back to UTF-8 with
    replacement characters (``lossy=True``).
    """
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        text = data.decode(encoding)
    except (SyntaxError, LookupError, UnicodeDecodeError):
        return data.decode("utf-8", errors="replace"), True
    if text.startswith("﻿"):
        text = text[1:]
    return text, False


def parse_source(source: str, path: str) -> FileIndex:
    """Index one file's source text. Parse failures become diagnostics, never exceptions."""
    try:
        tree = ast.parse(source, filename=path, type_comments=False)
    except RecursionError:
        return _failed(path, "recursion_limit", "Skipped: source is nested too deeply to parse.")
    except (SyntaxError, ValueError, MemoryError) as exc:
        detail = ""
        if isinstance(exc, SyntaxError) and exc.lineno:
            detail = f" at line {exc.lineno}"
        reason = exc.msg if isinstance(exc, SyntaxError) else type(exc).__name__
        return _failed(path, "parse_error", f"Could not parse{detail}: {reason}.")
    visitor = _IndexVisitor(path, source)
    try:
        visitor.visit(tree)
        visitor.resolve()
    except RecursionError:
        return _failed(path, "recursion_limit", "Skipped: source is nested too deeply to index.")
    return visitor.index


def _failed(path: str, code: str, message: str) -> FileIndex:
    index = FileIndex(path=path, parsed=False)
    index.diagnostics.append(Diagnostic(level="error", code=code, message=message, path=path))
    return index


def index_file(path: Path, root: Path) -> FileIndex:
    """Read, decode and index one discovered file."""
    rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
    try:
        data = path.read_bytes()
    except OSError as exc:
        index = FileIndex(path=rel, parsed=False)
        reason = exc.strerror or type(exc).__name__
        index.diagnostics.append(
            Diagnostic(
                level="warning",
                code="unreadable_file",
                message=f"Could not read ({reason}).",
                path=rel,
            )
        )
        return index
    source, lossy = decode_source(data)
    index = parse_source(source, rel)
    index.size_bytes = len(data)
    if lossy:
        index.diagnostics.insert(
            0,
            Diagnostic(
                level="warning",
                code="decode_replaced",
                message="File is not valid UTF-8; undecodable bytes were replaced.",
                path=rel,
            ),
        )
    return index


def build_index(
    target: str | os.PathLike[str],
    options: DiscoveryOptions | None = None,
    discovery: DiscoveryResult | None = None,
) -> RepoIndex:
    """Discover and index every candidate file under ``target``."""
    found = discovery if discovery is not None else discover(target, options)
    repo = RepoIndex(
        root=found.root,
        skipped=dict(found.skipped),
        diagnostics=list(found.diagnostics),
        total_bytes=found.total_bytes,
    )
    for path in found.files:
        file_index = index_file(path, found.root)
        repo.files.append(file_index)
        repo.diagnostics.extend(file_index.diagnostics)
    return repo
