"""Reasoning backend interface and production Antigravity CLI integration."""

import json
import re
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Protocol, Type, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class AntigravityBackendError(RuntimeError):
    """Raised when an error occurs during Antigravity reasoning execution."""

    def __init__(
        self,
        message: str,
        error_type: str = "EXECUTION_ERROR",
        command: Optional[list] = None,
        returncode: Optional[int] = None,
        stdout: str = "",
        stderr: str = "",
    ):
        super().__init__(f"[{error_type}] {message}")
        self.error_type = error_type
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ReasoningBackend(Protocol):
    """Abstract protocol for reasoning and structured generation backends."""

    def generate_structured(self, prompt: str, schema_cls: Type[T]) -> T:
        """Generate structured output adhering strictly to the provided Pydantic schema."""
        ...

    def generate_text(self, prompt: str) -> str:
        """Generate plain text output for reasoning tasks."""
        ...


class AntigravityCLIBackend:
    """Production reasoning backend interfacing directly with the local headless `agy` CLI.

    Executes structured generation using schema-enforced JSON output without external commercial API wrappers.
    """

    def __init__(
        self,
        cli_binary: str = "agy",
        model: Optional[str] = "gemini-3.7-flash-low",
        effort: Optional[str] = "low",
        timeout_seconds: int = 180,
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ):
        self.cli_binary = cli_binary
        self.model = model
        self.effort = effort
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    @staticmethod
    def _strip_markdown_fences(raw_text: str) -> str:
        """Remove ```json and ``` code block wrappers if present."""
        clean = raw_text.strip()
        if clean.startswith("```json"):
            clean = clean[7:]
        elif clean.startswith("```"):
            clean = clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        return clean.strip()

    @classmethod
    def _extract_all_json_objects(cls, raw_text: str) -> List[Any]:
        """Extract all valid JSON objects/arrays present in text (e.g. streaming events or line-delimited JSON)."""
        clean = cls._strip_markdown_fences(raw_text)
        decoder = json.JSONDecoder()
        objects = []
        pos = 0
        n = len(clean)

        while pos < n:
            idx_brace = clean.find("{", pos)
            idx_bracket = clean.find("[", pos)
            if idx_brace != -1 and idx_bracket != -1:
                next_start = min(idx_brace, idx_bracket)
            elif idx_brace != -1:
                next_start = idx_brace
            elif idx_bracket != -1:
                next_start = idx_bracket
            else:
                break

            try:
                obj, end_offset = decoder.raw_decode(clean[next_start:])
                objects.append(obj)
                pos = next_start + max(1, end_offset)
            except Exception:
                pos = next_start + 1

        return objects

    @classmethod
    def _extract_json_object(cls, raw_text: str) -> Any:
        """Robustly extract and decode the primary JSON object/array from string."""
        objects = cls._extract_all_json_objects(raw_text)
        if objects:
            return objects[-1]
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(cls._strip_markdown_fences(raw_text).strip())
        return obj

    def generate_structured(self, prompt: str, schema_cls: Type[T]) -> T:
        """Execute `agy` CLI with --json-schema file and return validated Pydantic model."""
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
            json.dump(schema_cls.model_json_schema(), tf)
            schema_file_path = tf.name

        cmd = [self.cli_binary]
        if self.model:
            cmd.extend(["--model", self.model])
        if self.effort:
            cmd.extend(["--effort", self.effort])
        cmd.extend([
            "--print",
            prompt,
            "--output-format",
            "json",
            "--json-schema",
            schema_file_path,
        ])

        res = None
        try:
            for attempt in range(self.max_retries):
                try:
                    res = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=self.timeout_seconds,
                    )
                except FileNotFoundError:
                    raise AntigravityBackendError(
                        message=f"Antigravity CLI binary '{self.cli_binary}' not found on system PATH.",
                        error_type="CLI_UNAVAILABLE",
                        command=cmd,
                    )
                except subprocess.TimeoutExpired as e:
                    if attempt < self.max_retries - 1:
                        time.sleep(self.retry_delay * (2 ** attempt))
                        continue
                    raise AntigravityBackendError(
                        message=f"Antigravity reasoning execution timed out after {self.timeout_seconds}s.",
                        error_type="TIMEOUT",
                        command=cmd,
                        stdout=e.stdout or "",
                        stderr=e.stderr or "",
                    )

                if res.returncode != 0:
                    err_details = res.stderr.strip() or res.stdout.strip()
                    if attempt < self.max_retries - 1 and ("error" in err_details.lower() or res.returncode == 1):
                        time.sleep(self.retry_delay * (2 ** attempt))
                        continue
                    error_type = "AUTH_ERROR" if "auth" in err_details.lower() or "login" in err_details.lower() else "EXECUTION_ERROR"
                    raise AntigravityBackendError(
                        message=f"Antigravity CLI failed with exit code {res.returncode}: {err_details}",
                        error_type=error_type,
                        command=cmd,
                        returncode=res.returncode,
                        stdout=res.stdout,
                        stderr=res.stderr,
                    )
                break
        finally:
            if os.path.exists(schema_file_path):
                try:
                    os.unlink(schema_file_path)
                except Exception:
                    pass

        try:
            # Robust extraction across streaming events, wrapper payloads, or direct output
            if not res or res.stdout is None:
                raise ValueError("Antigravity CLI returned empty or null output.")
            stdout_clean = res.stdout.strip()
            candidates = self._extract_all_json_objects(stdout_clean)
            if not candidates:
                raise ValueError("No valid JSON object found in CLI output.")

            last_error = None
            for cand in reversed(candidates):
                try:
                    return schema_cls.model_validate(cand)
                except Exception as ex:
                    last_error = ex

                if isinstance(cand, dict) and "response" in cand:
                    raw_response = cand["response"]
                    if isinstance(raw_response, (dict, list)):
                        try:
                            return schema_cls.model_validate(raw_response)
                        except Exception as ex:
                            last_error = ex
                    elif isinstance(raw_response, str):
                        inner_candidates = self._extract_all_json_objects(raw_response)
                        for inner in reversed(inner_candidates):
                            try:
                                return schema_cls.model_validate(inner)
                            except Exception as ex:
                                last_error = ex

            if last_error:
                raise last_error
            return schema_cls.model_validate(candidates[-1])
        except Exception as e:
            raise AntigravityBackendError(
                message=f"Failed to parse structured JSON output into schema '{schema_cls.__name__}': {str(e)}",
                error_type="INVALID_STRUCTURED_OUTPUT",
                command=cmd,
                stdout=res.stdout if res else "",
                stderr=res.stderr if res else "",
            )

    def generate_text(self, prompt: str) -> str:
        """Execute `agy` CLI for unstructured text generation."""
        cmd = [self.cli_binary]
        if self.model:
            cmd.extend(["--model", self.model])
        if self.effort:
            cmd.extend(["--effort", self.effort])
        cmd.extend(["--print", prompt, "--output-format", "json"])
        res = None
        for attempt in range(self.max_retries):
            try:
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_seconds,
                )
            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (2 ** attempt))
                    continue
                raise AntigravityBackendError(message=f"Antigravity CLI error: {str(e)}", error_type="EXECUTION_ERROR")

            if res.returncode != 0:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (2 ** attempt))
                    continue
                raise AntigravityBackendError(
                    message=f"Antigravity CLI failed: {res.stderr}",
                    error_type="EXECUTION_ERROR",
                    returncode=res.returncode,
                    stdout=res.stdout,
                    stderr=res.stderr,
                )
            break

        try:
            wrapper = json.loads(res.stdout)
            return wrapper.get("response", "").strip()
        except Exception:
            return res.stdout.strip()


class MockReasoningBackend:
    """Deterministic test double for unit testing without invoking the real CLI (TEST contract)."""

    def __init__(
        self,
        handler: Optional[Callable[[str, Type[BaseModel]], BaseModel]] = None,
        structured_responses: Optional[list] = None,
    ):
        self.handler = handler
        self.structured_responses = list(structured_responses) if structured_responses is not None else None

    def generate_structured(self, prompt: str, schema_cls: Type[T]) -> T:
        if self.structured_responses is not None:
            if self.structured_responses:
                resp = self.structured_responses.pop(0)
                if isinstance(resp, schema_cls):
                    return resp
                if isinstance(resp, dict):
                    return schema_cls.model_validate(resp)
            raise AntigravityBackendError("Mock reasoning backend ran out of structured responses", error_type="TEST_ERROR")

        if self.handler:
            result = self.handler(prompt, schema_cls)
            if isinstance(result, schema_cls):
                return result
            if isinstance(result, dict):
                return schema_cls.model_validate(result)

        try:
            return schema_cls.model_construct()
        except Exception:
            raise AntigravityBackendError("Mock reasoning backend unhandled schema", error_type="TEST_ERROR")

    def generate_text(self, prompt: str) -> str:
        return f"[TEST_OUTPUT] Response for: {prompt[:40]}"
