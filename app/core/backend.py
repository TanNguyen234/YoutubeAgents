"""Reasoning backend interface and production Antigravity CLI integration."""

import json
import re
import subprocess
from typing import Any, Callable, Dict, Optional, Protocol, Type, TypeVar
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

    def __init__(self, cli_binary: str = "agy", timeout_seconds: int = 120):
        self.cli_binary = cli_binary
        self.timeout_seconds = timeout_seconds

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
    def _extract_json_object(cls, raw_text: str) -> Any:
        """Robustly extract and decode the primary JSON object/array from string."""
        clean = cls._strip_markdown_fences(raw_text)

        # Find first opening brace or bracket
        idx_brace = clean.find("{")
        idx_bracket = clean.find("[")

        start_idx = 0
        if idx_brace != -1 and idx_bracket != -1:
            start_idx = min(idx_brace, idx_bracket)
        elif idx_brace != -1:
            start_idx = idx_brace
        elif idx_bracket != -1:
            start_idx = idx_bracket

        candidate = clean[start_idx:].strip()
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(candidate)
        return obj

    def generate_structured(self, prompt: str, schema_cls: Type[T]) -> T:
        """Execute `agy` CLI with --json-schema and return validated Pydantic model."""
        schema_json = json.dumps(schema_cls.model_json_schema())
        cmd = [
            self.cli_binary,
            "--print",
            prompt,
            "--output-format",
            "json",
            "--json-schema",
            schema_json,
        ]

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError:
            raise AntigravityBackendError(
                message=f"Antigravity CLI binary '{self.cli_binary}' not found on system PATH.",
                error_type="CLI_UNAVAILABLE",
                command=cmd,
            )
        except subprocess.TimeoutExpired as e:
            raise AntigravityBackendError(
                message=f"Antigravity reasoning execution timed out after {self.timeout_seconds}s.",
                error_type="TIMEOUT",
                command=cmd,
                stdout=e.stdout or "",
                stderr=e.stderr or "",
            )

        if res.returncode != 0:
            error_type = "AUTH_ERROR" if "auth" in res.stderr.lower() or "login" in res.stderr.lower() else "EXECUTION_ERROR"
            raise AntigravityBackendError(
                message=f"Antigravity CLI failed with exit code {res.returncode}: {res.stderr.strip()}",
                error_type=error_type,
                command=cmd,
                returncode=res.returncode,
                stdout=res.stdout,
                stderr=res.stderr,
            )

        try:
            # Parse wrapper output from agy
            stdout_clean = res.stdout.strip()
            wrapper_obj = self._extract_json_object(stdout_clean)

            if isinstance(wrapper_obj, dict) and "response" in wrapper_obj:
                raw_response = wrapper_obj["response"]
                if isinstance(raw_response, str):
                    clean_inner = self._strip_markdown_fences(raw_response)
                    if "{" in clean_inner or "[" in clean_inner:
                        inner_obj = self._extract_json_object(clean_inner)
                    else:
                        inner_obj = wrapper_obj
                elif isinstance(raw_response, (dict, list)):
                    inner_obj = raw_response
                else:
                    inner_obj = wrapper_obj
            else:
                inner_obj = wrapper_obj

            return schema_cls.model_validate(inner_obj)
        except Exception as e:
            raise AntigravityBackendError(
                message=f"Failed to parse structured JSON output into schema '{schema_cls.__name__}': {str(e)}",
                error_type="INVALID_STRUCTURED_OUTPUT",
                command=cmd,
                stdout=res.stdout,
                stderr=res.stderr,
            )

    def generate_text(self, prompt: str) -> str:
        """Execute `agy` CLI for unstructured text generation."""
        cmd = [self.cli_binary, "--print", prompt, "--output-format", "json"]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except Exception as e:
            raise AntigravityBackendError(message=f"Antigravity CLI error: {str(e)}", error_type="EXECUTION_ERROR")

        if res.returncode != 0:
            raise AntigravityBackendError(
                message=f"Antigravity CLI failed: {res.stderr}",
                error_type="EXECUTION_ERROR",
                returncode=res.returncode,
                stdout=res.stdout,
                stderr=res.stderr,
            )

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
