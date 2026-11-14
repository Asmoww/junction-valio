"""The European reader: the same prompts and schema through any OpenAI-compatible server (e.g. Gemma on Verda)."""
import json

import pytest

from gateway import ai


class Resp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self.content}}]}


def test_eu_reader_uses_the_eu_server_with_the_schema(monkeypatch):
    monkeypatch.setenv("READER", "eu")
    monkeypatch.setenv("EU_LLM_BASE_URL", "https://eu.example/v1")
    monkeypatch.setenv("EU_LLM_MODEL", "reader")
    monkeypatch.setenv("EU_LLM_LABEL", "Gemma on Verda")
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, body=json)
        return Resp('```json\n{"categories": ["10400"], "reason": "plain milk"}\n```')

    import httpx
    monkeypatch.setattr(httpx, "post", post)
    ai._type_cache.clear()
    out = ai.type_check("milk", {"10400": {"count": 3, "samples": ["Milk 1 l"]}, "10401": {"count": 2, "samples": ["Cream"]}})
    assert out["categories"] == ["10400"]
    assert seen["url"] == "https://eu.example/v1/chat/completions"
    assert "response_format" not in seen["body"]  # the schema is in the prompt, not a decoding grammar
    assert json.dumps(ai.TYPE_SCHEMA) in seen["body"]["messages"][0]["content"]
    assert ai.reader_name() == "Gemma on Verda (EU)"


def test_eu_reader_off_without_a_server(monkeypatch):
    monkeypatch.setenv("READER", "eu")
    monkeypatch.delenv("EU_LLM_BASE_URL", raising=False)
    assert not ai.eu()


def test_eu_answers_are_parsed_and_checked():
    """Gemma gets the schema in the prompt (a decoding grammar made it pad answers with whitespace for 60-90 s), so its
    answer is checked here: the first JSON object, with every required field, or a ModelError."""
    from gateway import ai
    schema = {"type": "object", "required": ["categories", "reason"]}
    assert ai._eu_json('```json\n{"categories": ["10400"], "reason": "milk"}\n```', schema) == '{"categories": ["10400"], "reason": "milk"}'
    for bad in ("", "no json here", '{"categories": ["10400"]}', '{"categories": [', "[1, 2]"):
        with pytest.raises(ai.ModelError):
            ai._eu_json(bad, schema)
