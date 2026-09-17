import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Protocol, Type, TypeVar
from pydantic import BaseModel

from app.core.backend import AntigravityBackendError

T = TypeVar("T", bound=BaseModel)


class VisualReasoningBackend(Protocol):
    """Abstract protocol for multimodal visual reasoning backends."""

    def evaluate_visual(
        self,
        prompt: str,
        image_paths: List[str],
        schema_cls: Type[T],
    ) -> T:
        """Evaluate one or more visual assets against a prompt and return schema-validated result."""
        ...


class AntigravityVisualBackend:
    """Production multimodal reasoning backend interfacing directly with local headless `agy` CLI.

    Executes structured visual inspection using verified local multimodal tool execution
    without external commercial AI API wrappers.
    """

    def __init__(
        self,
        cli_binary: str = "agy",
        model: Optional[str] = "gemini-3.8-flash-low",
        effort: Optional[str] = "low",
        timeout_seconds: int = 120,
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
        """Extract all valid JSON objects/arrays present in raw CLI output."""
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

    def evaluate_visual(
        self,
        prompt: str,
        image_paths: List[str],
        schema_cls: Type[T],
    ) -> T:
        """Execute `agy` CLI with image references inside an isolated disposable workspace."""
        if not image_paths:
            raise ValueError("evaluate_visual requires at least one image path")

        for img in image_paths:
            if not os.path.exists(img):
                raise FileNotFoundError(f"Visual asset file not found: '{img}'")

        # Security hardening: create a completely isolated disposable temporary workspace.
        # Copy ONLY candidate image frames and schema file into this workspace.
        # Run agy with cwd pointing to this directory so that prompt-injected or untrusted content
        # cannot inspect the repository or write files into the real workspace.
        with tempfile.TemporaryDirectory(prefix="agy_vqa_ws_") as isolated_ws:
            ws_path = Path(isolated_ws)

            # Copy input image(s) to isolated workspace
            isolated_image_names: List[str] = []
            for i, p in enumerate(image_paths):
                ext = Path(p).suffix or ".png"
                img_name = f"frame_{i}{ext}"
                target_img = ws_path / img_name
                shutil.copy2(p, target_img)
                isolated_image_names.append(img_name)

            # Write schema file directly inside isolated workspace
            schema_file_path = ws_path / "schema.json"
            with open(schema_file_path, "w", encoding="utf-8") as tf:
                json.dump(schema_cls.model_json_schema(), tf)

            # Build composite prompt referencing isolated local files
            if len(isolated_image_names) == 1:
                img_ref = f"Inspect the image file at '{isolated_image_names[0]}'. You are in read-only visual inspection mode. Do NOT invoke tools or execute shell commands. Produce the structured JSON assessment directly."
            else:
                frames_list = "\n".join(f"- Frame {i+1}: '{fn}'" for i, fn in enumerate(isolated_image_names))
                img_ref = f"Inspect the following representative video frames:\n{frames_list}\nYou are in read-only visual inspection mode. Do NOT invoke tools or execute shell commands. Produce the structured JSON assessment directly."

            full_prompt = f"{img_ref}\n\n{prompt}"

            cmd = [self.cli_binary]
            if self.model:
                cmd.extend(["--model", self.model])
            if self.effort:
                cmd.extend(["--effort", self.effort])

            # Security flags: execute in sandbox mode without slash commands or dangerous permissions
            cmd.extend([
                "--sandbox",
                "--disable-slash-commands",
                "--print",
                full_prompt,
                "--output-format",
                "json",
                "--json-schema",
                "schema.json",
            ])

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
                        cwd=str(ws_path),
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
                        message=f"Antigravity visual reasoning execution timed out after {self.timeout_seconds}s.",
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
                    raise AntigravityBackendError(
                        message=f"Antigravity CLI failed with exit code {res.returncode}: {err_details}",
                        error_type="EXECUTION_ERROR",
                        command=cmd,
                        returncode=res.returncode,
                        stdout=res.stdout,
                        stderr=res.stderr,
                    )
                break

        try:
            if not res or res.stdout is None:
                raise ValueError("Antigravity CLI returned empty or null output.")
            stdout_clean = res.stdout.strip()
            candidates = self._extract_all_json_objects(stdout_clean)
            if not candidates:
                raise ValueError("No valid JSON object found in CLI output.")

            # Search extracted objects (handle {"structured_output": ...} or {"response": ...})
            last_error = None
            for cand in reversed(candidates):
                # 1. Direct validation
                try:
                    return schema_cls.model_validate(cand)
                except Exception as ex:
                    last_error = ex

                # 2. structured_output wrapper
                if isinstance(cand, dict) and "structured_output" in cand:
                    st_out = cand["structured_output"]
                    if isinstance(st_out, (dict, list)):
                        try:
                            return schema_cls.model_validate(st_out)
                        except Exception as ex:
                            last_error = ex

                # 3. response wrapper
                if isinstance(cand, dict) and "response" in cand:
                    raw_resp = cand["response"]
                    if isinstance(raw_resp, (dict, list)):
                        try:
                            return schema_cls.model_validate(raw_resp)
                        except Exception as ex:
                            last_error = ex
                    elif isinstance(raw_resp, str):
                        inner_objects = self._extract_all_json_objects(raw_resp)
                        for inner in reversed(inner_objects):
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


class MockVisualReasoningBackend:
    """Deterministic test double for visual reasoning without invoking the CLI (TEST contract)."""

    def __init__(
        self,
        handler: Optional[Callable[[str, List[str], Type[BaseModel]], BaseModel]] = None,
        structured_responses: Optional[List[Any]] = None,
    ):
        self.handler = handler
        self.structured_responses = list(structured_responses) if structured_responses is not None else None
        self.invocations: List[Dict[str, Any]] = []

    def evaluate_visual(
        self,
        prompt: str,
        image_paths: List[str],
        schema_cls: Type[T],
    ) -> T:
        self.invocations.append({
            "prompt": prompt,
            "image_paths": image_paths,
            "schema_name": schema_cls.__name__,
        })

        if self.structured_responses is not None:
            if self.structured_responses:
                resp = self.structured_responses.pop(0)
                if isinstance(resp, Exception):
                    raise resp
                if isinstance(resp, schema_cls):
                    return resp
                if isinstance(resp, dict):
                    return schema_cls.model_validate(resp)
            raise AntigravityBackendError("Mock visual reasoning backend ran out of responses", error_type="TEST_ERROR")

        if self.handler:
            result = self.handler(prompt, image_paths, schema_cls)
            if isinstance(result, schema_cls):
                return result
            if isinstance(result, dict):
                return schema_cls.model_validate(result)

        try:
            return schema_cls.model_construct()
        except Exception:
            raise AntigravityBackendError("Mock visual reasoning unhandled schema", error_type="TEST_ERROR")
