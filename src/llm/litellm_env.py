# -*- coding: utf-8 -*-
"""Environment defaults that must be in place before ``import litellm``.

LiteLLM downloads its model price table from raw.githubusercontent.com
(BerriAI/litellm) at import time unless ``LITELLM_LOCAL_MODEL_COST_MAP`` is
truthy. We default it to ``True`` so the bundled price table is used and no
request leaves the machine on startup. An explicit value from the process
environment or the ``.env`` file (e.g. ``False``) is always respected.

Keep this module dependency-free (stdlib + python-dotenv only): it is imported
before litellm by every module that imports litellm at module level.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

LITELLM_LOCAL_MODEL_COST_MAP_ENV = "LITELLM_LOCAL_MODEL_COST_MAP"
LITELLM_LOCAL_MODEL_COST_MAP_DEFAULT = "True"


def _env_file_value(key: str) -> Optional[str]:
    env_file = os.getenv("ENV_FILE")
    env_path = Path(env_file) if env_file else Path(__file__).resolve().parents[2] / ".env"
    try:
        if not env_path.is_file():
            return None
        from dotenv import dotenv_values

        value = dotenv_values(env_path).get(key)
    except Exception:  # noqa: BLE001 - a broken .env must not break imports.
        return None
    return None if value is None else str(value)


def apply_litellm_env_defaults() -> None:
    """Default ``LITELLM_LOCAL_MODEL_COST_MAP=True`` unless the user set it."""
    if LITELLM_LOCAL_MODEL_COST_MAP_ENV in os.environ:
        return
    file_value = _env_file_value(LITELLM_LOCAL_MODEL_COST_MAP_ENV)
    os.environ[LITELLM_LOCAL_MODEL_COST_MAP_ENV] = (
        file_value if file_value is not None else LITELLM_LOCAL_MODEL_COST_MAP_DEFAULT
    )


apply_litellm_env_defaults()
