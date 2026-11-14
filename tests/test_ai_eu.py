"""The European reader: the same prompts and schema through any OpenAI-compatible server (e.g. Gemma on Verda)."""
import json

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
    assert seen["body"]["response_format"]["json_schema"]["schema"] == ai.TYPE_SCHEMA
    assert ai.reader_name() == "Gemma on Verda (EU)"


def test_eu_reader_off_without_a_server(monkeypatch):
    monkeypatch.setenv("READER", "eu")
    monkeypatch.delenv("EU_LLM_BASE_URL", raising=False)
    assert not ai.eu()
