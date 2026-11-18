"""Groq provider detection (phase G2, ADR-036). Sources are parsed, never run."""

from pathlib import Path

import pytest

from flightcheck.core.config import GROQ_DOCUMENTED_MODELS, GROQ_HOST
from flightcheck.core.models import Confidence
from flightcheck.scanner.detect import Signal, combine, detect, detect_providers, is_groq_env_name
from flightcheck.scanner.parser import (
    CLIENT_CONSTRUCTORS,
    GROQ_SDK_CONSTRUCTORS,
    RepoIndex,
    build_index,
    parse_source,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "detect_groq"
CONTRACT = Path(__file__).parents[2] / "docs" / "GROQ_CONTRACT.md"


def index_of(src: str, path: str = "app.py") -> RepoIndex:
    return RepoIndex(root=Path("."), files=[parse_source(src, path)])


def only_binding(result):
    (detection,) = result.bindings.values()
    return detection


def only_call(result):
    (detection,) = result.call_sites.values()
    return detection


# --- groq SDK clients --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "constructor"),
    [
        ("from groq import Groq\nc = Groq()\n", "groq.Groq"),
        ("from groq import AsyncGroq\nc = AsyncGroq()\n", "groq.AsyncGroq"),
        ("import groq\nc = groq.Groq()\n", "groq.Groq"),
        ("import groq as g\nc = g.AsyncGroq()\n", "groq.AsyncGroq"),
        ("from groq import Groq as G\nc = G()\n", "groq.Groq"),
    ],
)
def test_groq_sdk_clients_are_groq_high(src: str, constructor: str) -> None:
    result = detect(index_of(src))
    d = only_binding(result)
    assert (d.provider, d.confidence) == ("groq", Confidence.HIGH)
    assert d.signals == [f"groq SDK client ({constructor})"]
    (client,) = index_of(src).clients
    assert client.constructor == constructor
    assert client.is_async is ("Async" in constructor)


def test_groq_sdk_constructors_are_client_constructors() -> None:
    assert GROQ_SDK_CONSTRUCTORS == {"groq.Groq", "groq.AsyncGroq"}
    assert GROQ_SDK_CONSTRUCTORS <= CLIENT_CONSTRUCTORS


def test_groq_sdk_call_site_resolves_to_its_client() -> None:
    src = (
        "from groq import Groq\n"
        "client = Groq()\n"
        "def ask():\n"
        "    return client.chat.completions.create(model='m', messages=[])\n"
    )
    index = index_of(src)
    (call,) = index.call_sites
    assert call.kind == "chat.completions.create"
    assert call.binding is not None and call.binding.constructor == "groq.Groq"
    d = only_call(detect(index))
    assert (d.provider, d.confidence) == ("groq", Confidence.HIGH)


def test_groq_sdk_without_base_url_has_no_openai_default_signal() -> None:
    d = only_binding(detect(index_of("from groq import Groq\nc = Groq()\n")))
    assert not any("OpenAI SDK default" in s for s in d.signals)


def test_groq_sdk_with_a_proxy_base_url_stays_groq() -> None:
    src = "from groq import Groq\nc = Groq(base_url='http://127.0.0.1:8080')\n"
    d = only_binding(detect(index_of(src)))
    assert (d.provider, d.confidence) == ("groq", Confidence.HIGH)


def test_groq_sdk_pointed_at_bharatcode_is_a_conflict() -> None:
    src = "from groq import Groq\nc = Groq(base_url='https://bharatcode.ai/api/model/v1')\n"
    result = detect(index_of(src))
    d = only_binding(result)
    assert (d.provider, d.confidence) == ("unknown", Confidence.LOW)
    # One conflict note for the client, one for the repository (same signals).
    assert [diag.code for diag in result.diagnostics] == ["provider_conflict"] * 2


# --- OpenAI SDK and HTTP clients pointed at Groq ---------------------------------------------


def test_literal_groq_base_url_is_groq_high() -> None:
    src = "from openai import OpenAI\nc = OpenAI(base_url='https://api.groq.com/openai/v1')\n"
    d = only_binding(detect(index_of(src)))
    assert (d.provider, d.confidence) == ("groq", Confidence.HIGH)
    assert d.signals == [f"base_url contains {GROQ_HOST}"]


def test_groq_base_url_one_hop_name_is_groq_high() -> None:
    src = (
        "import openai\nGROQ = 'https://API.GROQ.COM/openai/v1'\nc = openai.OpenAI(base_url=GROQ)\n"
    )
    d = only_binding(detect(index_of(src)))
    assert (d.provider, d.confidence) == ("groq", Confidence.HIGH)


def test_groq_base_url_env_name_is_groq_high() -> None:
    src = "import os, openai\nc = openai.OpenAI(base_url=os.environ['GROQ_BASE_URL'])\n"
    d = only_binding(detect(index_of(src)))
    assert (d.provider, d.confidence) == ("groq", Confidence.HIGH)
    assert d.signals == ["base_url read from env var GROQ_BASE_URL"]


def test_groq_api_key_env_name_is_groq_medium() -> None:
    src = "import os, openai\nc = openai.OpenAI(api_key=os.getenv('GROQ_API_KEY'))\n"
    d = only_binding(detect(index_of(src)))
    assert (d.provider, d.confidence) == ("groq", Confidence.MEDIUM)


def test_groq_key_beats_unknown_base_url_of_equal_confidence() -> None:
    # Medium groq (key) vs medium openai_compatible_unknown (base_url): the fixed order wins.
    src = (
        "import os, openai\n"
        "c = openai.OpenAI(base_url=cfg.url, api_key=os.environ['GROQ_API_KEY'])\n"
    )
    d = only_binding(detect(index_of(src)))
    assert (d.provider, d.confidence) == ("groq", Confidence.MEDIUM)


def test_httpx_client_with_groq_base_url_is_groq() -> None:
    src = "import httpx2\nc = httpx2.Client(base_url='https://api.groq.com/openai/v1')\n"
    d = only_binding(detect(index_of(src)))
    assert (d.provider, d.confidence) == ("groq", Confidence.HIGH)


def test_raw_http_post_to_groq() -> None:
    src = "import httpx2\nhttpx2.post('https://api.groq.com/openai/v1/chat/completions', json={})\n"
    result = detect(index_of(src))
    d = only_call(result)
    assert (d.provider, d.confidence) == ("groq", Confidence.HIGH)
    assert f"raw HTTP post to {GROQ_HOST}" in d.signals


# --- what must NOT become groq (TRAP 3) ------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        "import os, openai\nc = openai.OpenAI(base_url=os.environ['OPENAI_BASE_URL'])\n",
        "import os, openai\nc = openai.OpenAI(base_url=os.environ['GROQUET_BASE_URL'])\n",
        "from openai import OpenAI\nc = OpenAI(base_url='https://llm.example.net/v1')\n",
        "from openai import OpenAI\nc = OpenAI(base_url='https://console.groq.com.example/v1')\n",
    ],
)
def test_generic_names_and_hosts_are_not_groq(src: str) -> None:
    d = only_binding(detect(index_of(src)))
    assert (d.provider, d.confidence) == ("openai_compatible_unknown", Confidence.MEDIUM)


def test_generic_key_env_is_not_groq() -> None:
    src = "import os, openai\nc = openai.OpenAI(api_key=os.environ['LLM_API_KEY'])\n"
    d = only_binding(detect(index_of(src)))
    assert (d.provider, d.confidence) == ("openai_or_env", Confidence.LOW)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("GROQ_API_KEY", True),
        ("GROQ_BASE_URL", True),
        ("groq_key", True),
        ("MY_GROQ_URL", True),
        ("GROQ", True),
        ("GROQUET_URL", False),
        ("OPENAI_BASE_URL", False),
        ("NOGROQ_KEY", False),
        ("", False),
        (None, False),
    ],
)
def test_groq_env_name_needs_the_whole_word(name: str | None, expected: bool) -> None:
    assert is_groq_env_name(name) is expected


# --- models, declarations, tie order ---------------------------------------------------------


def test_groq_model_is_supporting_only() -> None:
    src = (
        "from openai import OpenAI\n"
        "c = OpenAI(base_url='https://llm.example.net/v1')\n"
        "c.chat.completions.create(model='openai/gpt-oss-20b', messages=[])\n"
    )
    d = only_call(detect(index_of(src)))
    assert d.provider == "openai_compatible_unknown"
    assert "model 'openai/gpt-oss-20b' is a Groq model" in d.signals


def test_groq_model_lifts_sdk_default_client() -> None:
    src = (
        "from openai import OpenAI\n"
        "c = OpenAI()\n"
        "c.chat.completions.create(model='llama-3.3-70b-versatile', messages=[])\n"
    )
    d = only_call(detect(index_of(src)))
    assert (d.provider, d.confidence) == ("groq", Confidence.MEDIUM)


def test_bharatcode_model_ids_are_not_groq_models() -> None:
    # BharatCode's "qwen-3.8-27b" and Groq's "qwen/qwen3.8-27b" are different IDs.
    assert "qwen-3.8-27b" not in GROQ_DOCUMENTED_MODELS
    assert "qwen/qwen3.8-27b" in GROQ_DOCUMENTED_MODELS


def test_declared_groq_provider() -> None:
    src = "from openai import OpenAI\nc = OpenAI(base_url='https://llm.example.net/v1')\n"
    d = only_binding(detect(index_of(src), declared_provider="groq"))
    assert (d.provider, d.confidence) == ("groq", Confidence.HIGH)
    assert "flightcheck.toml provider = groq" in d.signals


def test_declared_groq_against_a_bharatcode_url_is_a_conflict() -> None:
    src = "from openai import OpenAI\nc = OpenAI(base_url='https://bharatcode.ai/api/model/v1')\n"
    d = only_binding(detect(index_of(src), declared_provider="groq"))
    assert (d.provider, d.confidence) == ("unknown", Confidence.LOW)


def test_tie_order_puts_bharatcode_before_groq_before_the_rest() -> None:
    medium = Confidence.MEDIUM
    assert combine([Signal("groq", medium, "a"), Signal("bharatcode", medium, "b")])[0] == (
        "bharatcode"
    )
    for other in ("openai_compatible_unknown", "openai_or_env", "unknown"):
        assert combine([Signal(other, medium, "a"), Signal("groq", medium, "b")])[0] == "groq"


def test_unresolved_call_site_takes_repo_level_groq_at_low() -> None:
    src = (
        "from groq import Groq\n"
        "c = Groq()\n"
        "def f(client):\n"
        "    return client.chat.completions.create(model=m, messages=[])\n"
    )
    result = detect(index_of(src))
    assert result.repo.provider == "groq"
    d = only_call(result)
    assert (d.provider, d.confidence) == ("groq", Confidence.LOW)
    assert d.signals == ["receiver not resolved; repo-level provider is groq"]


# --- fixture directory -------------------------------------------------------------------------


def test_fixture_directory_detection() -> None:
    index = build_index(FIXTURES)
    detections = detect_providers(index)
    by_call = {
        (d.location.path, d.location.line): (d.provider, d.confidence)
        for d in detections
        if d.location.path != "."
    }
    high, medium = Confidence.HIGH, Confidence.MEDIUM
    assert by_call[("groq_sdk.py", 8)] == ("groq", high)  # Groq() call site
    assert by_call[("groq_sdk.py", 16)] == ("groq", high)  # aliased AsyncGroq
    assert by_call[("groq_module_alias.py", 6)] == ("groq", high)
    assert by_call[("openai_literal_groq.py", 6)] == ("groq", high)
    assert by_call[("openai_env_groq.py", 6)] == ("groq", high)
    assert by_call[("openai_key_only.py", 6)] == ("groq", medium)
    assert by_call[("raw_http.py", 7)] == ("groq", high)  # raw post (receiver unresolved)
    assert by_call[("raw_http.py", 10)] == ("groq", high)  # httpx2.Client binding
    assert by_call[("unknown_host.py", 6)] == ("openai_compatible_unknown", medium)
    assert by_call[("generic_env.py", 6)] == ("openai_compatible_unknown", medium)
    repo = detections[-1]
    assert repo.location.path == "."
    assert (repo.provider, repo.confidence) == ("groq", high)
    assert detect(index).diagnostics == []


def test_documented_groq_models_are_in_the_contract() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    for model in GROQ_DOCUMENTED_MODELS:
        assert f"`{model}`" in text, model


def test_winning_signals_come_first_phase_g2() -> None:
    # The terminal shows signals[0] as the reason; it must explain the classification even
    # when another provider's signal was collected first (mixed repositories).
    src = (
        "import os\n"
        "from openai import OpenAI\n"
        "other = OpenAI(base_url=os.environ['OPENAI_BASE_URL'])\n"
        "groq = OpenAI(base_url='https://api.groq.com/openai/v1')\n"
        "groq.chat.completions.create(model='openai/gpt-oss-20b', messages=[])\n"
    )
    result = detect(index_of(src))
    assert result.repo.provider == "groq"
    assert result.repo.signals[0] == f"base_url contains {GROQ_HOST}"
    assert "base_url set from env var OPENAI_BASE_URL without bharatcode.ai" in result.repo.signals
    # Direct signals come before supporting ones of the same provider.
    call = only_call(result)
    assert call.signals.index(f"base_url contains {GROQ_HOST}") < call.signals.index(
        "model 'openai/gpt-oss-20b' is a Groq model"
    )


def test_conflicts_keep_the_original_signal_order() -> None:
    src = "from groq import Groq\nc = Groq(base_url='https://bharatcode.ai/api/model/v1')\n"
    d = only_binding(detect(index_of(src)))
    assert d.signals == ["base_url contains bharatcode.ai", "groq SDK client (groq.Groq)"]
