"""Tests for OpenAI-compatible provider diagnostics."""

import pytest

from code_agent.utils import api_diagnostics
from code_agent.utils.api_diagnostics import ApiDiagnosticError, list_models


class FakeResponse:
    """Context manager that mimics urllib responses."""

    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_list_models_extracts_openai_compatible_ids(monkeypatch) -> None:
    """The /models probe should parse standard OpenAI-compatible payloads."""

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://api.example.com/v1/models"
        assert request.headers["Authorization"] == "Bearer test-key"
        assert timeout == 20
        return FakeResponse(
            b'{"data":[{"id":"gpt-4.1-mini"},{"id":"gpt-4o-mini"}]}'
        )

    monkeypatch.setattr(api_diagnostics, "urlopen", fake_urlopen)

    result = list_models("https://api.example.com/v1/", "test-key")

    assert result.models == ["gpt-4.1-mini", "gpt-4o-mini"]
    assert result.contains("gpt-4.1-mini")


def test_list_models_rejects_empty_payload(monkeypatch) -> None:
    """Empty model lists should be reported as provider diagnostics."""

    def fake_urlopen(request, timeout):
        return FakeResponse(b'{"data":[]}')

    monkeypatch.setattr(api_diagnostics, "urlopen", fake_urlopen)

    with pytest.raises(ApiDiagnosticError, match="no model IDs"):
        list_models("https://api.example.com/v1", "test-key")
