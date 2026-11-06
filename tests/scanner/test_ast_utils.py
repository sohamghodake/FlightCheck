import ast

import pytest

from flightcheck.scanner.ast_utils import (
    Unknown,
    attr_chain,
    literal_value,
    source_segment,
    split_attrs,
    string_fragments,
)


def expr(text: str) -> ast.expr:
    return ast.parse(text, mode="eval").body


def test_attr_chain_full():
    assert attr_chain(expr("client.chat.completions.create")) == [
        "client",
        "chat",
        "completions",
        "create",
    ]


def test_attr_chain_name_only():
    assert attr_chain(expr("client")) == ["client"]


@pytest.mark.parametrize("text", ["OpenAI().responses.create", "x[0].y", "(lambda: c)().z"])
def test_attr_chain_non_name_base_is_none(text):
    assert attr_chain(expr(text)) is None


def test_split_attrs_keeps_call_base():
    base, attrs = split_attrs(expr("OpenAI().responses.create"))
    assert isinstance(base, ast.Call)
    assert attrs == ["responses", "create"]


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("True", True),
        ("None", None),
        ("'qwen-3.8-27b'", "qwen-3.8-27b"),
        ("-1", -1),
        ("[1, 'a']", [1, "a"]),
        ("(1, 2)", (1, 2)),
        ("{'a': {'b': 2}}", {"a": {"b": 2}}),
        ("{1, 2}", {1, 2}),
    ],
)
def test_literal_value_literals(text, value):
    assert literal_value(expr(text), text) == value


@pytest.mark.parametrize("text", ["flag", "cfg.store", "f(x)", "[x]", "{'a': y}", "1 + 2", "-x"])
def test_literal_value_non_literals_are_unknown(text):
    value = literal_value(expr(text), text)
    assert isinstance(value, Unknown)
    assert value.source == text


def test_source_segment_truncates():
    source = "x = '" + "a" * 500 + "'"
    node = ast.parse(source).body[0].value
    seg = source_segment(source, node, max_len=50)
    assert len(seg) == 50
    assert seg.endswith("…")


def test_source_segment_falls_back_to_unparse():
    node = expr("a.b")
    assert source_segment("", node) == "a.b"


@pytest.mark.parametrize(
    ("text", "fragments"),
    [
        (
            "'https://bharatcode.ai/api/model/v1/responses'",
            ["https://bharatcode.ai/api/model/v1/responses"],
        ),
        ("f'{base}/responses'", ["/responses"]),
        ("base + '/chat/completions'", ["/chat/completions"]),
        ("'%s/responses' % base", ["%s/responses"]),
        ("'{}/responses'.format(base)", ["{}/responses"]),
        ("url", []),
        ("42", []),
    ],
)
def test_string_fragments(text, fragments):
    assert string_fragments(expr(text)) == fragments


def test_string_fragments_resolves_names_one_hop():
    consts = {"BASE": "https://bharatcode.ai/api/model/v1"}
    assert "".join(string_fragments(expr("f'{BASE}/responses'"), consts.get)) == (
        "https://bharatcode.ai/api/model/v1/responses"
    )
