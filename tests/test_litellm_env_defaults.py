# -*- coding: utf-8 -*-
"""LITELLM_LOCAL_MODEL_COST_MAP must be set before litellm is imported.

LiteLLM fetches its model price table from raw.githubusercontent.com at import
time unless LITELLM_LOCAL_MODEL_COST_MAP is truthy. These tests run in a fresh
interpreter so the import order is the real one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

_PROBE = textwrap.dedent(
    """
    import json, os, socket, sys

    hosts = []
    _orig_getaddrinfo = socket.getaddrinfo

    def _guard(host, *args, **kwargs):
        hosts.append(str(host))
        raise OSError("network disabled in test: %s" % host)

    socket.getaddrinfo = _guard
    target = sys.argv[1]
    before = "litellm" in sys.modules
    if target == "main":
        import main  # noqa: F401 - project CLI entry
    elif target == "src.llm.litellm_env":
        import src.llm.litellm_env  # noqa: F401
    else:
        import importlib
        importlib.import_module(target)
    print(json.dumps({
        "hosts": hosts,
        "value": os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP"),
        "litellm_preloaded": before,
        "litellm_loaded": "litellm" in sys.modules,
    }))
    """
)


def _run_probe(tmp_path: Path, target: str, *, env_file_text: str = "", extra_env=None) -> dict:
    env_file = tmp_path / ".env"
    env_file.write_text(env_file_text, encoding="utf-8")
    # Drop proxy settings so getaddrinfo sees the real target host instead of the proxy.
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "LITELLM_LOCAL_MODEL_COST_MAP" and "proxy" not in key.lower()
    }
    env.update(
        {
            "ENV_FILE": str(env_file),
            "PYTHONPATH": str(REPO_ROOT),
            "LOG_DIR": str(tmp_path / "logs"),
            "DATABASE_PATH": str(tmp_path / "data" / "test.db"),
        }
    )
    env.update(extra_env or {})
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE, target],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("target", ["src.analyzer", "src.agent.llm_adapter", "main"])
def test_importing_entrypoints_does_not_fetch_litellm_price_table(tmp_path: Path, target: str) -> None:
    result = _run_probe(tmp_path, target)

    assert result["litellm_preloaded"] is False
    assert result["value"] == "True"
    assert not any("githubusercontent" in host for host in result["hosts"]), result["hosts"]
    if target != "main":
        assert result["litellm_loaded"] is True


def test_probe_detects_litellm_remote_fetch_when_explicitly_disabled(tmp_path: Path) -> None:
    """Control: with an explicit False, importing litellm does try GitHub."""
    result = _run_probe(
        tmp_path,
        "litellm",
        extra_env={"LITELLM_LOCAL_MODEL_COST_MAP": "False"},
    )

    assert result["value"] == "False"
    assert any("githubusercontent" in host for host in result["hosts"]), result["hosts"]


def test_explicit_process_env_value_is_respected(tmp_path: Path) -> None:
    result = _run_probe(
        tmp_path,
        "src.llm.litellm_env",
        extra_env={"LITELLM_LOCAL_MODEL_COST_MAP": "False"},
    )

    assert result["value"] == "False"
    assert result["litellm_loaded"] is False


def test_explicit_env_file_value_is_respected(tmp_path: Path) -> None:
    result = _run_probe(
        tmp_path,
        "src.llm.litellm_env",
        env_file_text="LITELLM_LOCAL_MODEL_COST_MAP=False\n",
    )

    assert result["value"] == "False"
    assert result["litellm_loaded"] is False


def test_config_import_applies_default_before_setup_env(tmp_path: Path) -> None:
    result = _run_probe(tmp_path, "src.config")

    assert result["value"] == "True"
    assert result["litellm_loaded"] is False


def test_dockerfile_sets_local_model_cost_map() -> None:
    dockerfile = (REPO_ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert "ENV LITELLM_LOCAL_MODEL_COST_MAP=True" in dockerfile
