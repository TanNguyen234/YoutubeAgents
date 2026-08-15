"""Spike test for Google Antigravity SDK and CLI integration."""

import asyncio
import json
import os
import subprocess
import sys


def test_import():
    """Test importing google.antigravity module."""
    try:
        import google.antigravity as ag
        from google.antigravity import Agent, CapabilitiesConfig, LocalAgentConfig
        print("IMPORT_STATUS: SUCCESS")
        print("EXPORTED_SYMBOLS:", [attr for attr in dir(ag) if not attr.startswith("_")])
        return True
    except Exception as e:
        print(f"IMPORT_STATUS: FAILED ({type(e).__name__}: {e})")
        return False


def test_cli_headless_json():
    """Test calling `agy` CLI in headless mode with json output format."""
    try:
        cmd = ["agy", "--print", "Say hello in JSON format with key 'greeting'", "--output-format", "json"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        print("CLI_STATUS: SUCCESS" if res.returncode == 0 else f"CLI_STATUS: RETURNCODE_{res.returncode}")
        print("CLI_STDOUT:", res.stdout.strip()[:200])
        print("CLI_STDERR:", res.stderr.strip()[:200])
        return res.returncode == 0
    except Exception as e:
        print(f"CLI_STATUS: FAILED ({type(e).__name__}: {e})")
        return False


if __name__ == "__main__":
    print("=== Running Antigravity Runtime Spike Tests ===")
    import_ok = test_import()
    cli_ok = test_cli_headless_json()
    print(f"Summary: import={import_ok}, cli={cli_ok}")
