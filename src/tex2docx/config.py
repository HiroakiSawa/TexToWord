"""Configuration loading: packaged defaults deep-merged with a user YAML file."""

from __future__ import annotations

import copy
import hashlib
import json
import platform
from importlib import resources
from pathlib import Path

import yaml


class ConfigError(ValueError):
    pass


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def default_config_text() -> str:
    return resources.files("tex2docx").joinpath("data/default_config.yaml").read_text(encoding="utf-8")


def load_config(path: Path | None = None) -> dict:
    cfg = yaml.safe_load(default_config_text())
    if path is not None:
        try:
            user = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        except FileNotFoundError as exc:
            raise ConfigError(f"設定ファイルが見つかりません: {path}") from exc
        except yaml.YAMLError as exc:
            raise ConfigError(f"設定ファイルの YAML が不正です: {path}: {exc}") from exc
        if not isinstance(user, dict):
            raise ConfigError(f"設定ファイルの最上位はマッピングである必要があります: {path}")
        cfg = _deep_merge(cfg, user)
    return cfg


def config_fingerprint(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def current_os() -> str:
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Windows":
        return "windows"
    return "linux"


def target_os(cfg: dict) -> str:
    value = cfg["fonts"].get("target_os", "auto")
    return current_os() if value in (None, "auto") else value
