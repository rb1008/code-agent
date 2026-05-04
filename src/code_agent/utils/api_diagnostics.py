"""Provider diagnostics for OpenAI-compatible endpoints."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ApiDiagnosticError(RuntimeError):
    """Raised when provider diagnostics cannot complete."""


@dataclass(frozen=True)
class ModelListResult:
    """Result returned by an OpenAI-compatible model list probe."""

    base_url: str
    models: list[str]

    def contains(self, model_name: str) -> bool:
        """Return whether the provider advertised the configured model."""
        return model_name in self.models


def list_models(base_url: str, api_key: str, timeout: int = 20) -> ModelListResult:
    """Fetch available model IDs from an OpenAI-compatible ``/models`` endpoint."""
    url = f"{base_url.rstrip('/')}/models"
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "Code-Agent/0.1.0",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as e:
        body = _safe_error_body(e)
        raise ApiDiagnosticError(f"HTTP {e.code} while listing models: {body}") from e
    except URLError as e:
        raise ApiDiagnosticError(f"Network error while listing models: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise ApiDiagnosticError(f"Provider returned invalid JSON: {e}") from e
    except OSError as e:
        raise ApiDiagnosticError(f"I/O error while listing models: {e}") from e

    models = _extract_model_ids(payload)
    if not models:
        raise ApiDiagnosticError("Provider returned no model IDs from /models.")
    return ModelListResult(base_url=base_url, models=models)


def _extract_model_ids(payload: Any) -> list[str]:
    """Extract model IDs from common OpenAI-compatible response shapes."""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return sorted(
                str(item["id"])
                for item in data
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            )

        models = payload.get("models")
        if isinstance(models, list):
            return sorted(
                str(item["id"])
                for item in models
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            )

    return []


def _safe_error_body(error: HTTPError, limit: int = 500) -> str:
    """Read a bounded HTTP error body for human diagnostics."""
    try:
        body = error.read().decode("utf-8", errors="replace").strip()
    except Exception:
        body = error.reason or ""
    if len(body) > limit:
        body = f"{body[:limit]}..."
    return body or error.reason or "no response body"
