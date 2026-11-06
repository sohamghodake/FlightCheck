import json
import shutil
import time
from pathlib import Path

import pytest

from flightcheck.scanner.ast_utils import Unknown
from flightcheck.scanner.parser import build_index, decode_source, index_file, parse_source

FIXTURES = Path(__file__).parent.parent / "fixtures" / "parse"


@pytest.fixture(scope="module")
def repo():
    return build_index(FIXTURES)


def calls(repo, path):
    return repo.file(path).call_sites


# --- alias forms ----------------------------------------------------------------------------


def test_import_module_alias(repo):
    f = repo.file("aliases.py")
    assert f.imports == {"oa": "openai"}
    (client,) = f.clients
    assert client.constructor == "openai.OpenAI"
    assert client.target == "client"
    assert client.scope == "<module>"
    assert client.kwargs == {
        "base_url": "https://bharatcode.ai/api/model/v1",
        "timeout": 30,
        "max_retries": 2,
    }
    (call,) = f.call_sites
    assert call.kind == "responses.create"
    assert call.location.line == 7
    assert call.location.column == 11
    assert call.binding is client
    assert call.resolution == "module"
    assert call.function == "ask"
    assert call.params["model"] == "qwen-3.8-27b"
    assert call.params["store"] is False
    assert isinstance(call.params["input"], Unknown)


def test_from_import_alias_and_unknown_flag(repo):
    f = repo.file("from_import_alias.py")
    assert f.imports["C"] == "openai.OpenAI"
    (client,) = f.clients
    assert client.env_kwargs == {"api_key": "BHARATCODE_API_KEY"}
    (call,) = f.call_sites
    assert call.kind == "chat.completions.create"
    assert call.endpoint == "chat.completions"
    assert call.location.line == 9
    assert call.binding is client
    assert call.params["store"] == Unknown("flag")


def test_self_client_and_try_detection(repo):
    f = repo.file("self_client.py")
    (client,) = f.clients
    assert client.target == "self.client"
    assert client.class_name == "Agent"
    assert client.env_kwargs == {"base_url": "BHARATCODE_BASE_URL"}
    protected, unprotected = f.call_sites
    assert protected.resolution == unprotected.resolution == "class_attr"
    assert protected.binding is client and unprotected.binding is client
    assert protected.in_try is True
    assert protected.try_handlers == ["openai.APIError", "TimeoutError"]
    assert protected.function == "Agent.run"
    assert protected.class_name == "Agent"
    assert unprotected.in_try is False
    assert unprotected.try_handlers == []
    assert unprotected.kind == "responses.stream"
    assert unprotected.stream is True


def test_inline_constructor(repo):
    (call,) = calls(repo, "inline_ctor.py")
    assert call.resolution == "inline"
    assert call.binding is not None
    assert call.binding.target is None
    assert call.receiver == "OpenAI()"
    assert call.stream is True


def test_raw_http_posts(repo):
    f = repo.file("raw_http.py")
    responses, chat = f.call_sites
    assert responses.kind == chat.kind == "http.post"
    assert responses.endpoint == "responses"
    assert responses.url == "https://bharatcode.ai/api/model/v1/responses"
    assert responses.params == {
        "model": "qwen-3.8-27b",
        "input": Unknown("payload_input"),
        "stream": False,
    }
    assert responses.stream is False
    assert responses.receiver == "httpx2"
    assert responses.binding is None
    assert chat.endpoint == "chat.completions"
    assert chat.url.endswith("/chat/completions")
    assert len(f.call_sites) == 2  # the upload post is not an API call


def test_env_reads(repo):
    reads = {(r.name, r.kind, r.has_default) for r in repo.file("env_reads.py").env_reads}
    assert reads == {
        ("BHARATCODE_API_KEY", "environ_subscript", False),
        ("BHARATCODE_BASE_URL", "environ_get", True),
        ("LLM_MODEL", "getenv", False),
        ("REGION", "environ_get", False),
        ("DEBUG", "getenv", True),
    }


def test_nested_async_lambda_walrus(repo):
    f = repo.file("nested.py")
    by_fn = {c.function: c for c in f.call_sites}
    inner = by_fn["outer.<locals>.inner"]
    assert inner.is_async_function is True
    assert inner.resolution == "function"  # bound in the enclosing function
    assert inner.binding.constructor == "openai.AsyncOpenAI"
    assert inner.binding.is_async is True
    lam = by_fn["lambda_receiver"]
    assert lam.binding is None and lam.resolution is None
    walrus = by_fn["walrus"]
    assert walrus.binding is None  # walrus assignments are not bindings (documented)
    assert by_fn["unknown_param"].binding is None
    assert "outer.<locals>.inner" in f.functions


def test_index_integration_counts(repo):
    counts = repo.counts()
    assert counts == {
        "files": 9,
        "parsed": 9,
        "imports": 14,
        "clients": 7,
        "call_sites": 11,
        "env_reads": 7,
        "functions": 15,
        "string_literals": 30,
        "diagnostics": 0,
    }
    assert json.dumps([f.to_dict() for f in repo.files])  # serialisable for --verbose dumps


# --- facts ----------------------------------------------------------------------------------


def test_function_facts_attributes_and_strings():
    src = (
        "def handle(client):\n"
        "    r = client.responses.create(model='m')\n"
        "    if r.status == 'incomplete':\n"
        "        return r.incomplete_details\n"
        "    def helper():\n"
        "        return r.output_text\n"
    )
    f = parse_source(src, "f.py")
    facts = f.functions["handle"]
    assert {"status", "incomplete_details", "output_text"} <= facts.attributes
    assert "incomplete" in facts.strings
    assert "output_text" in f.functions["handle.<locals>.helper"].attributes
    assert "incomplete_details" not in f.functions["handle.<locals>.helper"].attributes


def test_decorators_are_qualified():
    src = (
        "import tenacity\n"
        "from openai import OpenAI\n"
        "client = OpenAI()\n"
        "@tenacity.retry(stop=None)\n"
        "def call():\n"
        "    return client.responses.create(model='m')\n"
    )
    (call,) = parse_source(src, "d.py").call_sites
    assert call.decorators == ["tenacity.retry"]


def test_def_inside_try_is_not_protected():
    src = (
        "from openai import OpenAI\n"
        "client = OpenAI()\n"
        "try:\n"
        "    def f():\n"
        "        return client.responses.create(model='m')\n"
        "except Exception:\n"
        "    pass\n"
    )
    (call,) = parse_source(src, "t.py").call_sites
    assert call.in_try is False


def test_call_in_handler_or_finally_not_in_try():
    src = (
        "def f(client):\n"
        "    try:\n"
        "        pass\n"
        "    except ValueError:\n"
        "        client.responses.create(model='a')\n"
        "    finally:\n"
        "        client.responses.create(model='b')\n"
    )
    assert [c.in_try for c in parse_source(src, "h.py").call_sites] == [False, False]


def test_bare_except_and_nested_try():
    src = (
        "def f(client):\n"
        "    try:\n"
        "        try:\n"
        "            client.responses.create(model='a')\n"
        "        except KeyError:\n"
        "            pass\n"
        "    except:\n"
        "        pass\n"
    )
    (call,) = parse_source(src, "b.py").call_sites
    assert call.try_handlers == ["KeyError", "BaseException"]


def test_rebinding_picks_latest_before_call():
    src = (
        "from openai import OpenAI\n"
        "def f():\n"
        "    client = OpenAI()\n"
        "    client.responses.create(model='a')\n"
        "    client = OpenAI(base_url='https://bharatcode.ai/api/model/v1')\n"
        "    client.responses.create(model='b')\n"
    )
    first, second = parse_source(src, "r.py").call_sites
    assert "base_url" not in first.binding.kwargs
    assert second.binding.kwargs["base_url"].startswith("https://bharatcode.ai")


def test_self_binding_defined_after_use_and_class_body_attr():
    src = (
        "from openai import OpenAI\n"
        "class A:\n"
        "    shared = OpenAI()\n"
        "    def run(self):\n"
        "        return self.client.responses.create(model='m')\n"
        "    def use_shared(self):\n"
        "        return self.shared.responses.create(model='m')\n"
        "    def __init__(self):\n"
        "        self.client = OpenAI(base_url='x')\n"
    )
    run, shared = parse_source(src, "c.py").call_sites
    assert run.binding.target == "self.client"
    assert shared.binding.target == "shared"
    assert shared.resolution == "class_attr"


def test_beta_and_with_statement_and_star_kwargs():
    src = (
        "import openai\n"
        "def f(opts):\n"
        "    with openai.OpenAI() as client:\n"
        "        client.beta.chat.completions.parse(model='m', **opts)\n"
    )
    (call,) = parse_source(src, "w.py").call_sites
    assert call.kind == "chat.completions.parse"
    assert call.binding is not None and call.resolution == "function"
    assert call.has_star_kwargs is True


def test_stream_flag_unknown():
    (call,) = parse_source("client.responses.create(stream=s)\n", "s.py").call_sites
    assert call.stream is None


def test_unimported_constructor_is_not_a_client():
    f = parse_source("client = OpenAI()\nclient.responses.create(model='m')\n", "u.py")
    assert f.clients == []
    assert f.call_sites[0].binding is None


# --- error handling and encodings ----------------------------------------------------------


def test_syntax_error_becomes_diagnostic():
    f = parse_source("def broken(:\n", "bad.py")
    assert f.parsed is False
    (diag,) = f.diagnostics
    assert diag.level == "error"
    assert diag.code == "parse_error"
    assert diag.path == "bad.py"
    assert "line 1" in diag.message


def test_null_bytes_in_source_become_diagnostic():
    f = parse_source("x = 1\x00\n", "nul.py")
    assert f.parsed is False
    assert f.diagnostics[0].code == "parse_error"


def test_deep_nesting_becomes_recursion_diagnostic():
    f = parse_source("x = " + "1+" * 200_000 + "1\n", "deep.py")
    assert f.parsed is False
    assert f.diagnostics[0].code == "recursion_limit"


def test_non_utf8_file_is_replaced_with_warning(tmp_path):
    path = tmp_path / "latin.py"
    path.write_bytes(
        b"# caf\xe9\nfrom openai import OpenAI\nOpenAI().responses.create(model='m')\n"
    )
    f = index_file(path, tmp_path)
    assert f.parsed is True
    assert f.diagnostics[0].code == "decode_replaced"
    assert f.diagnostics[0].level == "warning"
    assert len(f.call_sites) == 1


def test_coding_cookie_is_honoured():
    text, lossy = decode_source(b"# -*- coding: latin-1 -*-\nname = 'caf\xe9'\n")
    assert lossy is False
    assert "café" in text


def test_bom_file(tmp_path):
    path = tmp_path / "bom.py"
    path.write_bytes(b"\xef\xbb\xbfimport openai\nopenai.OpenAI().responses.create(model='m')\n")
    f = index_file(path, tmp_path)
    assert f.parsed is True
    assert f.diagnostics == []
    assert f.call_sites[0].location.line == 2


def test_tabs_indentation():
    src = "def f(c):\n\ttry:\n\t\tc.responses.create(model='m')\n\texcept Exception:\n\t\tpass\n"
    (call,) = parse_source(src, "tabs.py").call_sites
    assert call.in_try is True
    assert call.location.line == 3


def test_build_index_continues_after_bad_files(tmp_path):
    shutil.copytree(FIXTURES, tmp_path / "repo")
    (tmp_path / "repo" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    (tmp_path / "repo" / "latin.py").write_bytes(b"s = '\xff'\n")
    repo = build_index(tmp_path / "repo")
    assert len(repo.files) == 11
    codes = sorted(d.code for d in repo.diagnostics)
    assert codes == ["decode_replaced", "parse_error"]
    assert len(repo.call_sites) == 11


def test_single_file_target_index():
    repo = build_index(FIXTURES / "aliases.py")
    assert [f.path for f in repo.files] == ["aliases.py"]


@pytest.mark.slow
def test_index_200_files_under_3_seconds(tmp_path):
    body = (FIXTURES / "self_client.py").read_text(encoding="utf-8")
    for i in range(200):
        pkg = tmp_path / f"pkg{i % 10}"
        pkg.mkdir(exist_ok=True)
        (pkg / f"mod_{i}.py").write_text(body + f"\nVALUE_{i} = {i}\n", encoding="utf-8")
    start = time.perf_counter()
    repo = build_index(tmp_path)
    elapsed = time.perf_counter() - start
    assert len(repo.files) == 200
    assert len(repo.call_sites) == 400
    assert elapsed < 3.0
