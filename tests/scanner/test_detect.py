import ast
from pathlib import Path

from flightcheck.core.models import Confidence
from flightcheck.scanner.detect import (
    BHARATCODE_DOCUMENTED_MODELS,
    Signal,
    combine,
    detect,
    detect_providers,
)
from flightcheck.scanner.parser import RepoIndex, build_index, parse_source

FIXTURES = Path(__file__).parent.parent / "fixtures" / "parse"
CONTRACT = Path(__file__).parents[2] / "docs" / "BHARATCODE_CONTRACT.md"


def index_of(src: str, path: str = "app.py") -> RepoIndex:
    return RepoIndex(root=Path("."), files=[parse_source(src, path)])


def only_binding(result):
    (detection,) = result.bindings.values()
    return detection


def test_bharatcode_via_literal_url():
    src = "import openai\nc = openai.OpenAI(base_url='https://bharatcode.ai/api/model/v1')\n"
    d = only_binding(detect(index_of(src)))
    assert d.provider == "bharatcode"
    assert d.confidence is Confidence.HIGH
    assert d.signals == ["base_url contains bharatcode.ai"]


def test_bharatcode_via_base_url_env_name():
    src = "import os, openai\nc = openai.OpenAI(base_url=os.environ['BHARATCODE_BASE_URL'])\n"
    d = only_binding(detect(index_of(src)))
    assert (d.provider, d.confidence) == ("bharatcode", Confidence.HIGH)


def test_bharatcode_via_env_name_one_hop():
    src = (
        "import os, openai\n"
        "url = os.getenv('BHARATCODE_BASE_URL') or 'http://localhost'\n"
        "c = openai.OpenAI(base_url=url)\n"
    )
    d = only_binding(detect(index_of(src)))
    assert (d.provider, d.confidence) == ("bharatcode", Confidence.HIGH)


def test_bharatcode_via_api_key_env_is_medium():
    src = "import os, openai\nc = openai.OpenAI(api_key=os.getenv('BHARATCODE_API_KEY'))\n"
    d = only_binding(detect(index_of(src)))
    assert (d.provider, d.confidence) == ("bharatcode", Confidence.MEDIUM)


def test_openai_or_env_without_hints():
    src = "from openai import OpenAI\nc = OpenAI()\nc.responses.create(model='gpt-x')\n"
    result = detect(index_of(src))
    d = only_binding(result)
    assert (d.provider, d.confidence) == ("openai_or_env", Confidence.LOW)
    (call,) = result.call_sites.values()
    assert call.provider == "openai_or_env"
    assert result.repo.provider == "openai_or_env"


def test_other_base_url_is_openai_compatible_unknown():
    src = "from openai import OpenAI\nc = OpenAI(base_url='https://api.groq.com/openai/v1')\n"
    d = only_binding(detect(index_of(src)))
    assert (d.provider, d.confidence) == ("openai_compatible_unknown", Confidence.MEDIUM)


def test_model_is_supporting_only():
    src = (
        "from openai import OpenAI\n"
        "c = OpenAI(base_url='https://llm.example.com/v1')\n"
        "c.responses.create(model='qwen-3.8-27b')\n"
    )
    (call,) = detect(index_of(src)).call_sites.values()
    assert call.provider == "openai_compatible_unknown"
    assert "model 'qwen-3.8-27b' is a BharatCode model" in call.signals


def test_model_lifts_sdk_default_client():
    src = "from openai import OpenAI\nc = OpenAI()\nc.responses.create(model='qwen-3.8-27b')\n"
    (call,) = detect(index_of(src)).call_sites.values()
    assert (call.provider, call.confidence) == ("bharatcode", Confidence.MEDIUM)


def test_declared_provider_from_config():
    src = "from openai import OpenAI\nc = OpenAI(base_url='https://llm.example.com/v1')\n"
    d = only_binding(detect(index_of(src), declared_provider="bharatcode"))
    assert (d.provider, d.confidence) == ("bharatcode", Confidence.HIGH)
    assert "flightcheck.toml provider = bharatcode" in d.signals


def test_raw_http_post_to_bharatcode():
    src = "import httpx2\nhttpx2.post('https://bharatcode.ai/api/model/v1/responses', json={})\n"
    result = detect(index_of(src))
    (call,) = result.call_sites.values()
    assert (call.provider, call.confidence) == ("bharatcode", Confidence.HIGH)


def test_unresolved_call_site_uses_repo_provider_at_low():
    src = (
        "from openai import OpenAI\n"
        "c = OpenAI(base_url='https://bharatcode.ai/api/model/v1')\n"
        "def f(client):\n"
        "    return client.responses.create(model=m)\n"
    )
    result = detect(index_of(src))
    assert result.repo.provider == "bharatcode"
    (call,) = result.call_sites.values()
    assert (call.provider, call.confidence) == ("bharatcode", Confidence.LOW)
    assert call.signals == ["receiver not resolved; repo-level provider is bharatcode"]


def test_high_conflict_gives_unknown():
    signals = [
        Signal("bharatcode", Confidence.HIGH, "a"),
        Signal("openai_compatible_unknown", Confidence.HIGH, "b"),
    ]
    assert combine(signals) == ("unknown", Confidence.LOW, True)


def test_highest_confidence_wins():
    signals = [
        Signal("openai_or_env", Confidence.LOW, "a"),
        Signal("bharatcode", Confidence.MEDIUM, "b"),
    ]
    assert combine(signals) == ("bharatcode", Confidence.MEDIUM, False)


def test_empty_repo_has_no_repo_detection():
    result = detect(index_of("x = 1\n"))
    assert result.repo is None
    assert result.as_list() == []


def test_fixture_directory_detection():
    detections = detect_providers(build_index(FIXTURES))
    repo = detections[-1]
    assert repo.location.path == "."
    assert (repo.provider, repo.confidence) == ("bharatcode", Confidence.HIGH)
    by_line = {(d.location.path, d.location.line): d for d in detections}
    assert by_line[("inline_ctor.py", 5)].provider == "openai_or_env"
    assert by_line[("raw_http.py", 8)].provider == "bharatcode"
    assert by_line[("nested.py", 10)].provider == "openai_compatible_unknown"


def test_documented_models_match_contract():
    text = CONTRACT.read_text(encoding="utf-8")
    for model in BHARATCODE_DOCUMENTED_MODELS:
        assert f"`{model}`" in text


def test_scanner_imports_no_execution_modules():
    forbidden = {"subprocess", "importlib", "runpy", "flightcheck.providers", "flightcheck.runtime"}
    package = Path(__file__).parents[2] / "src" / "flightcheck" / "scanner"
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"exec", "eval", "compile", "__import__"}, path
                continue
            else:
                continue
            for name in names:
                assert not any(name == f or name.startswith(f + ".") for f in forbidden), (
                    path,
                    name,
                )
