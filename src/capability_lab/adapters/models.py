from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from capability_lab.domain.models import ModelIdentity


class OllamaError(RuntimeError):
    pass


class OllamaUnreachableError(OllamaError):
    pass


class OllamaMalformedResponseError(OllamaError):
    pass


class OllamaModelMissingError(OllamaError):
    pass


class OllamaModelAmbiguousError(OllamaError):
    pass


class OllamaDigestChangedError(OllamaError):
    pass


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class OllamaModelAdapter:
    def __init__(self, base_url: str, model: str, timeout_seconds: float) -> None:
        self.base_url = base_url
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._opener = build_opener(ProxyHandler({}), _RejectRedirects())

    def identity(self, expected_digest: str | None = None) -> ModelIdentity:
        version = self._request("/api/version").get("version")
        if version is not None and not isinstance(version, str):
            raise OllamaMalformedResponseError("/api/version: version must be a string")

        tags = self._request("/api/tags")
        models = tags.get("models")
        if not isinstance(models, list) or not all(isinstance(item, dict) for item in models):
            raise OllamaMalformedResponseError("/api/tags: models must be a list of objects")
        matches = [item for item in models if item.get("name") == self.model]
        if not matches:
            raise OllamaModelMissingError(f"model not installed: {self.model}")
        if len(matches) > 1:
            raise OllamaModelAmbiguousError(f"multiple exact model matches: {self.model}")
        digest = matches[0].get("digest")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise OllamaMalformedResponseError(
                "/api/tags: model digest must be lowercase bare SHA-256"
            )
        if expected_digest is not None and digest != expected_digest:
            raise OllamaDigestChangedError(
                f"model digest changed for {self.model}: expected {expected_digest}, got {digest}"
            )

        shown = self._request("/api/show", {"model": self.model})
        details = shown.get("details")
        capabilities = shown.get("capabilities")
        if not isinstance(details, dict):
            raise OllamaMalformedResponseError("/api/show: details must be an object")
        if not isinstance(capabilities, list) or not all(
            isinstance(capability, str) for capability in capabilities
        ):
            raise OllamaMalformedResponseError("/api/show: capabilities must be a list of strings")
        return ModelIdentity(
            provider="ollama",
            name=self.model,
            digest=digest,
            format=self._detail(details, "format"),
            family=self._detail(details, "family"),
            parameter_size=self._detail(details, "parameter_size"),
            quantization_level=self._detail(details, "quantization_level"),
            capabilities=tuple(capabilities),
            server_version=version,
        )

    def _request(self, path: str, body: dict[str, str] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        request = Request(
            self.base_url.rstrip("/") + path,
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method="POST" if data is not None else "GET",
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise OllamaUnreachableError(f"Ollama request failed at {path}: {exc}") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OllamaMalformedResponseError(f"{path}: malformed JSON") from exc
        if not isinstance(payload, dict):
            raise OllamaMalformedResponseError(f"{path}: response must be an object")
        return payload

    @staticmethod
    def _detail(details: dict[str, Any], key: str) -> str:
        value = details.get(key)
        if not isinstance(value, str) or not value:
            raise OllamaMalformedResponseError(f"/api/show: {key} must be a string")
        return value
