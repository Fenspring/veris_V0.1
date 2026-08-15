"""Model-agnostic inference layer.

The hospital chooses the model. Veris must run on a frontier cloud API or on a
7B model served by Ollama on a workstation inside the hospital's own network,
because for many customers the deciding factor is that policy text never leaves
the building, not accuracy.

Two consequences shape this module and the rest of the pipeline:

1. No vendor SDKs. Every provider here speaks HTTP through the standard library,
   so Veris has no dependency that can pin it to one vendor and nothing that
   breaks in an air-gapped install.

2. Assume the weakest plausible model. That means small context windows, no
   guarantee of tool use, no guarantee of JSON mode, and unreliable instruction
   following. The pipeline compensates structurally rather than by prompting
   harder: tasks are small, outputs are line-oriented rather than JSON, and
   every output is mechanically verified against the source text before it is
   allowed to become a claim (see ingest.verify_span). A model that hallucinates
   a quote fails the span check and its output is discarded, which is what makes
   a weak local model safe to use here.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class ModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelInfo:
    provider: str
    name: str
    context_tokens: int


class Model(Protocol):
    """The entire surface Veris is allowed to assume a model has."""

    info: ModelInfo

    def complete(self, system: str, prompt: str, *, max_tokens: int = 2048) -> str: ...


def _post(url: str, payload: dict, headers: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ModelError(f"{url} -> {e.code}: {e.read().decode('utf-8')[:400]}") from e
    except urllib.error.URLError as e:
        raise ModelError(f"{url} unreachable: {e.reason}") from e


class OpenAICompatModel:
    """Anything speaking /v1/chat/completions.

    This is the lingua franca of local inference: Ollama, vLLM, LM Studio,
    llama.cpp's server, text-generation-webui, plus OpenAI, Together, Groq,
    Fireworks and most hosted providers. Supporting this one protocol is what
    makes "the hospital chooses" a real option rather than a slogan.
    """

    def __init__(self, base_url: str, model: str, api_key: str = "", context_tokens: int = 8192):
        self.base_url = base_url.rstrip("/")
        self.info = ModelInfo("openai-compat", model, context_tokens)
        self._key = api_key

    def complete(self, system: str, prompt: str, *, max_tokens: int = 2048) -> str:
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        data = _post(
            f"{self.base_url}/chat/completions",
            {
                "model": self.info.name,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": 0,
            },
            headers,
        )
        return data["choices"][0]["message"]["content"]


class AnthropicModel:
    def __init__(self, model: str, api_key: str, base_url: str = "https://api.anthropic.com",
                 context_tokens: int = 200_000):
        self.base_url = base_url.rstrip("/")
        self.info = ModelInfo("anthropic", model, context_tokens)
        self._key = api_key

    def complete(self, system: str, prompt: str, *, max_tokens: int = 2048) -> str:
        data = _post(
            f"{self.base_url}/v1/messages",
            {
                "model": self.info.name,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0,
            },
            {"x-api-key": self._key, "anthropic-version": "2023-06-01"},
        )
        return "".join(b.get("text", "") for b in data["content"])


def call_id(system: str, prompt: str) -> str:
    return hashlib.sha256(f"{system}\x00{prompt}".encode("utf-8")).hexdigest()[:20]


class RecordedModel:
    """Replays model responses stored on disk, keyed by the hash of the call.

    Three jobs, which is why it earns its place rather than being a test stub:

    - It lets an agent (or a human) act as the model today, with no API key,
      by writing the response file directly. The pipeline does not know or care.
    - It makes evaluation reproducible. Scoring runs over recorded artifacts, so
      a result can be re-derived months later without paying for inference or
      depending on a model version that no longer exists.
    - It is a cache. Re-running a pipeline after changing only downstream code
      costs nothing.

    A missing recording raises with the call id and the prompt written to disk,
    so the gap can be filled and the run resumed.
    """

    def __init__(self, path: Path, name: str = "recorded", fallback: Model | None = None):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.info = ModelInfo("recorded", name, 0)
        self._fallback = fallback

    def complete(self, system: str, prompt: str, *, max_tokens: int = 2048) -> str:
        cid = call_id(system, prompt)
        response = self.path / f"{cid}.response.txt"
        if response.exists():
            return response.read_text(encoding="utf-8")

        if self._fallback is not None:
            out = self._fallback.complete(system, prompt, max_tokens=max_tokens)
            response.write_text(out, encoding="utf-8")
            (self.path / f"{cid}.request.json").write_text(
                json.dumps({"system": system, "prompt": prompt,
                            "model": self._fallback.info.name}, indent=2),
                encoding="utf-8",
            )
            return out

        (self.path / f"{cid}.request.json").write_text(
            json.dumps({"system": system, "prompt": prompt}, indent=2), encoding="utf-8"
        )
        raise ModelError(
            f"No recording for call {cid}. The request has been written to "
            f"{self.path / (cid + '.request.json')}; write the model's reply to "
            f"{response} and re-run."
        )


def from_env() -> Model:
    """Build the configured model. Defaults to a local Ollama endpoint, because
    the on-premises case is the one that must work without ceremony."""
    provider = os.environ.get("VERIS_MODEL_PROVIDER", "recorded").lower()
    name = os.environ.get("VERIS_MODEL", "")
    ctx = int(os.environ.get("VERIS_MODEL_CONTEXT", "8192"))

    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ModelError("VERIS_MODEL_PROVIDER=anthropic requires ANTHROPIC_API_KEY")
        return AnthropicModel(name or "claude-sonnet-5", key,
                              os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"))

    if provider in ("openai", "openai-compat", "ollama", "local", "vllm"):
        base = os.environ.get("VERIS_MODEL_BASE_URL", "http://localhost:11434/v1")
        return OpenAICompatModel(base, name or "llama3.1:8b",
                                 os.environ.get("VERIS_MODEL_API_KEY", ""), ctx)

    if provider == "recorded":
        path = Path(os.environ.get("VERIS_RECORDINGS", "data/recordings"))
        return RecordedModel(path, name or "recorded")

    raise ModelError(f"Unknown VERIS_MODEL_PROVIDER: {provider!r}")
