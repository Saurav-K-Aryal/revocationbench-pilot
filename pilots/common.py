"""Output and loopback inference utilities; no cloud credentials or downloads."""
from __future__ import annotations

import csv
import hashlib
import ipaddress
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")


def write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False,
                               allow_nan=False, default=_json_default) + "\n")
    return path


def loopback_url(endpoint: str) -> str:
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use an HTTP loopback server URL without credentials, query or fragment.")
    host = parsed.hostname or ""
    if host != "localhost":
        try:
            valid = ipaddress.ip_address(host).is_loopback
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("The pilot model endpoint must be on loopback (localhost/127.0.0.1/::1).")
    return endpoint.rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Model redirects are disabled", headers, fp)


def local_request(url: str, payload: Any | None = None, timeout: float = 120) -> dict:
    loopback_url(url)
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(req, timeout=timeout) as response:
        body = response.read(5_000_001)
    if len(body) > 5_000_000:
        raise ValueError("Local model returned an unexpectedly large response.")
    return json.loads(body)


@dataclass
class PilotContext:
    output_dir: Path
    seed: int = 2026
    profile: str = "smoke"
    model: str | None = None
    endpoint: str = "http://127.0.0.1:11434/v1"
    max_model_calls: int = 8
    timeout_seconds: float = 120
    cases_file: Path | None = None
    model_calls: int = field(default=0, init=False)
    model_provenance: dict = field(default_factory=dict, init=False)

    def __post_init__(self):
        self.output_dir = Path(self.output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.endpoint = loopback_url(self.endpoint)
        if self.max_model_calls < 1:
            raise ValueError("max_model_calls must be positive")
        if self.model and "cloud" in self.model.lower():
            raise ValueError("Use a downloaded local model; cloud-tagged models are outside this pilot.")

    def path(self, filename: str) -> Path:
        path = (self.output_dir / filename).resolve()
        if not path.is_relative_to(self.output_dir) or path == self.output_dir:
            raise ValueError("Experiment artifacts must stay inside the experiment output directory.")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(self, filename: str, value: Any) -> Path:
        return write_json(self.path(filename), value)

    def write_csv(self, filename: str, rows: list[dict]) -> Path:
        path = self.path(filename)
        columns = list(dict.fromkeys(key for row in rows for key in row))
        with path.open("w", newline="") as stream:
            if columns:
                writer = csv.DictWriter(stream, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
        return path

    def _inspect_model(self):
        if self.model_provenance:
            return
        models = local_request(self.endpoint + "/models", timeout=min(self.timeout_seconds, 15))
        ids = [entry.get("id") for entry in models.get("data", [])]
        if self.model not in ids:
            raise ValueError(f"Model {self.model!r} is not listed by the local server. Available: {ids}")
        provenance = {"requested_model": self.model, "endpoint": self.endpoint,
                      "server_model_record": next((x for x in models.get("data", []) if x.get("id") == self.model), {})}
        # Ollama exposes a local-weight digest; other compatible servers may not.
        parsed = urllib.parse.urlsplit(self.endpoint)
        origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        try:
            tags = local_request(origin + "/api/tags", timeout=10)
            record = next((x for x in tags.get("models", []) if x.get("name") == self.model or x.get("model") == self.model), {})
            provenance["local_weight_record"] = record
        except (urllib.error.URLError, ValueError, KeyError):
            provenance["local_weight_digest"] = "not exposed by compatible server"
        self.model_provenance = provenance
        self.write_json("model_provenance.json", provenance)

    def local_model(self, prompt: str, system: str | None = None) -> str:
        if not self.model:
            raise ValueError("No local model selected; optional model metrics must be reported as skipped.")
        if self.model_calls >= self.max_model_calls:
            raise RuntimeError("Local model call cap reached; request a larger explicit cap or fewer cases.")
        self._inspect_model()
        self.model_calls += 1
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        payload = {"model": self.model, "messages": messages, "temperature": 0,
                   "max_tokens": 192, "stream": False, "seed": self.seed}
        started = time.perf_counter()
        record = {"call": self.model_calls, "request": payload,
                  "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
        try:
            response = local_request(self.endpoint + "/chat/completions", payload, self.timeout_seconds)
            answer = response["choices"][0]["message"]["content"]
            if not isinstance(answer, str):
                raise ValueError("Expected text content in model response")
            record.update({"response": response, "status": "completed"})
            return answer
        except Exception as exc:
            record.update({"status": "error", "error": str(exc)})
            raise
        finally:
            record["elapsed_seconds"] = time.perf_counter() - started
            with self.path("model_calls.jsonl").open("a") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False, default=_json_default) + "\n")

    def model_runtime_observation(self) -> dict:
        """Best-effort server-reported allocation; this is not whole-system peak RAM."""
        if not self.model_calls:
            return {"status": "not used"}
        parsed = urllib.parse.urlsplit(self.endpoint)
        origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        try:
            payload = local_request(origin + "/api/ps", timeout=10)
            records = [x for x in payload.get("models", []) if x.get("name") == self.model or x.get("model") == self.model]
            return {"status": "observed" if records else "model not listed", "records": records,
                    "limitation": "Server-reported current allocation only; excludes other applications and is not a peak measurement."}
        except (urllib.error.URLError, ValueError, KeyError) as exc:
            return {"status": "not exposed by compatible server", "detail": str(exc)}
