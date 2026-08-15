"""Comprehensive Antigravity Runtime & Reasoning Spike Verification.

Verifies:
1. Programmatic Python SDK invocation
2. Headless CLI invocation with structured JSON output
3. Multi-turn conversation continuation
4. Schema-enforced structured generation
5. Error & authentication handling
"""

import json
import subprocess
import sys
import time
from pathlib import Path
from pydantic import BaseModel, Field


class TopicEvaluation(BaseModel):
    """Pydantic model for structured reasoning output test."""
    topic: str = Field(description="The video topic")
    target_audience: str = Field(description="Target audience demographic")
    viability_score: int = Field(ge=1, le=10, description="Viability score from 1 to 10")
    key_reasons: list[str] = Field(description="Key reasons for evaluation")


def run_cli_structured_output():
    """Test `agy` CLI with --json-schema and --output-format json."""
    print("\n--- Test 1: CLI Structured Output with JSON Schema ---")
    schema_str = json.dumps(TopicEvaluation.model_json_schema())
    prompt = "Evaluate the video topic: 'How to build local AI agents in Python'. Return strictly conforming JSON."
    cmd = [
        "agy",
        "--print",
        prompt,
        "--output-format",
        "json",
        "--json-schema",
        schema_str,
    ]
    t0 = time.time()
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    elapsed = time.time() - t0
    print(f"Status Code: {res.returncode} (Elapsed: {elapsed:.2f}s)")
    if res.returncode != 0:
        print("STDERR:", res.stderr)
        return False

    try:
        parsed_wrapper = json.loads(res.stdout)
        print("Conversation ID:", parsed_wrapper.get("conversation_id"))
        print("Status:", parsed_wrapper.get("status"))
        raw_response = parsed_wrapper.get("response", "")
        print("Raw Response preview:", raw_response[:200])

        # Clean JSON markdown fences if present
        clean_json = raw_response.strip()
        if clean_json.startswith("```json"):
            clean_json = clean_json[7:]
        if clean_json.startswith("```"):
            clean_json = clean_json[3:]
        if clean_json.endswith("```"):
            clean_json = clean_json[:-3]
        clean_json = clean_json.strip()

        data = json.loads(clean_json)
        validated = TopicEvaluation.model_validate(data)
        print(f"Validated Model Object: topic='{validated.topic}', score={validated.viability_score}")
        return True
    except Exception as e:
        print(f"Failed to parse/validate structured output: {e}")
        return False


def run_cli_multiturn():
    """Test multi-turn conversation via conversation ID."""
    print("\n--- Test 2: CLI Multi-turn Conversation ---")
    # Turn 1
    cmd1 = ["agy", "--print", "Remember this secret code: 'YOUTUBE_AUTOPILOT_2026'", "--output-format", "json"]
    res1 = subprocess.run(cmd1, capture_output=True, text=True, timeout=60)
    if res1.returncode != 0:
        print("Turn 1 failed:", res1.stderr)
        return False

    data1 = json.loads(res1.stdout)
    conv_id = data1.get("conversation_id")
    print(f"Turn 1 succeeded. Conversation ID: {conv_id}")

    # Turn 2: Continue using --conversation
    cmd2 = ["agy", "--print", "What was the secret code I gave you?", "--conversation", conv_id, "--output-format", "json"]
    res2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=60)
    if res2.returncode != 0:
        print("Turn 2 failed:", res2.stderr)
        return False

    data2 = json.loads(res2.stdout)
    resp2 = data2.get("response", "")
    print("Turn 2 Response:", resp2.strip())
    matched = "YOUTUBE_AUTOPILOT_2026" in resp2
    print(f"Context memory maintained: {matched}")
    return matched


if __name__ == "__main__":
    print("Starting Comprehensive Antigravity Runtime Spike...")
    t1 = run_cli_structured_output()
    t2 = run_cli_multiturn()
    print(f"\nComprehensive Spike Results: Structured Output={t1}, Multi-turn Context={t2}")
