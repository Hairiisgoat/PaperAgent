"""Lazy, secret-safe configuration for the GLM chat completion endpoint."""
import math
import os
import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import dotenv_values
from app.core.config import ENV_FILE, ConfigurationError


@dataclass(frozen=True)
class LLMSettings:
    api_key: str = field(repr=False)
    model: str
    base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    timeout_seconds: float = 30.0
    planner_read_timeout_seconds: float = 120.0


def validate_base_url(base: str) -> None:
    """Validate the origin and port without retaining the value in errors."""
    invalid = False
    try:
        url = urlsplit(base)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError
        if url.netloc.endswith(':') or (url.port is not None and not 1 <= url.port <= 65535):
            raise ValueError
        host = url.hostname
        try:
            ipaddress.ip_address(host)
        except ValueError:
            labels = host.encode('idna').decode('ascii').rstrip('.').split('.')
            if len(host) > 253 or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in labels):
                raise ValueError
        if any(ord(char) < 33 or ord(char) == 127 for char in base):
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        invalid = True
    if invalid:
        raise ConfigurationError("Invalid LLM configuration")


def load_llm_settings(env_file: Path = ENV_FILE) -> LLMSettings:
    """Load only on model use; environment overrides file, no interpolation."""
    failed = False
    try:
        values = dotenv_values(env_file, interpolate=False, encoding="utf-8-sig")
    except (OSError, UnicodeError):
        values = {}
        failed = True
    if failed:
        raise ConfigurationError("LLM configuration unavailable")
    def get(name: str, default: str = "") -> str:
        return (os.environ.get(name, values.get(name)) or default).strip()
    key, model = get("LLM_API_KEY"), get("LLM_MODEL")
    base = get("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    if not key or not model:
        raise ConfigurationError("LLM_API_KEY and LLM_MODEL are required for model calls")
    if any(c in key for c in "\r\n"):
        raise ConfigurationError("Invalid LLM configuration")
    validate_base_url(base)
    invalid = False
    try:
        timeout = float(get("LLM_TIMEOUT_SECONDS", "30"))
        planner_timeout = float(get("LLM_PLANNER_READ_TIMEOUT_SECONDS", "120"))
        if not math.isfinite(timeout) or not 0 < timeout <= 600 or not math.isfinite(planner_timeout) or not 0 < planner_timeout <= 600:
            raise ValueError
    except (ValueError, TypeError):
        invalid = True
    if invalid:
        raise ConfigurationError("Invalid LLM configuration")
    return LLMSettings(api_key=key, model=model, base_url=base, timeout_seconds=timeout, planner_read_timeout_seconds=planner_timeout)
