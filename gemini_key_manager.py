from __future__ import annotations

from pathlib import Path
from typing import Optional


ENV_PATH = Path(__file__).with_name(".env")
ACTIVE_KEY_VAR = "GEMINI_API_KEY_ACTIVE"
DEFAULT_KEY_VAR = "GEMINI_API_KEY"
KEY_PREFIX = "GEMINI_API_KEY_"


def _read_env_lines(env_path: Path = ENV_PATH) -> list[str]:
    if not env_path.exists():
        return []
    return env_path.read_text(encoding="utf-8").splitlines()


def _parse_env_map(env_path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in _read_env_lines(env_path):
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _normalize_key_name(name: str) -> str:
    return str(name or "").strip().lower()


def list_gemini_keys(env_path: Path = ENV_PATH) -> dict[str, str]:
    env_map = _parse_env_map(env_path)
    keys: dict[str, str] = {}
    for key, value in env_map.items():
        if key == DEFAULT_KEY_VAR or key == ACTIVE_KEY_VAR:
            continue
        if not key.startswith(KEY_PREFIX):
            continue
        suffix = key[len(KEY_PREFIX):].strip().lower()
        if suffix:
            keys[suffix] = value
    if DEFAULT_KEY_VAR in env_map and "default" not in keys:
        keys["default"] = env_map[DEFAULT_KEY_VAR]
    return keys


def get_active_key_name(env_path: Path = ENV_PATH) -> str:
    env_map = _parse_env_map(env_path)
    explicit = _normalize_key_name(env_map.get(ACTIVE_KEY_VAR, ""))
    if explicit:
        return explicit
    keys = list_gemini_keys(env_path)
    if "default" in keys:
        return "default"
    return next(iter(keys.keys()), "")


def get_active_key(env_path: Path = ENV_PATH) -> str:
    keys = list_gemini_keys(env_path)
    active_name = get_active_key_name(env_path)
    if active_name and active_name in keys:
        return keys[active_name]
    return keys.get("default", "")


def switch_active_key(name: str, env_path: Path = ENV_PATH) -> tuple[str, str]:
    normalized_name = _normalize_key_name(name)
    keys = list_gemini_keys(env_path)
    if normalized_name not in keys:
        raise ValueError(f"未找到名为 '{name}' 的 Gemini key")

    active_key = keys[normalized_name]
    lines = _read_env_lines(env_path)
    updated_lines = []
    seen_default = False
    seen_active = False
    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith(f"{DEFAULT_KEY_VAR}="):
            updated_lines.append(f"{DEFAULT_KEY_VAR}={active_key}")
            seen_default = True
        elif stripped.startswith(f"{ACTIVE_KEY_VAR}="):
            updated_lines.append(f"{ACTIVE_KEY_VAR}={normalized_name}")
            seen_active = True
        else:
            updated_lines.append(raw_line)

    if not seen_default:
        updated_lines.append(f"{DEFAULT_KEY_VAR}={active_key}")
    if not seen_active:
        updated_lines.append(f"{ACTIVE_KEY_VAR}={normalized_name}")

    env_path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")
    return normalized_name, active_key


def ensure_named_key(name: str, key_value: str, env_path: Path = ENV_PATH) -> None:
    normalized_name = _normalize_key_name(name)
    var_name = f"{KEY_PREFIX}{normalized_name.upper()}"
    lines = _read_env_lines(env_path)
    updated_lines = []
    seen = False
    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith(f"{var_name}="):
            updated_lines.append(f"{var_name}={key_value}")
            seen = True
        else:
            updated_lines.append(raw_line)
    if not seen:
        updated_lines.append(f"{var_name}={key_value}")
    env_path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Gemini API key 管理工具")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("list", help="列出已命名的 Gemini keys")

    switch_parser = subparsers.add_parser("switch", help="切换当前启用的 Gemini key")
    switch_parser.add_argument("name", help="要切换到的 key 名称")

    args = parser.parse_args()

    if args.command == "list":
        keys = list_gemini_keys()
        active_name = get_active_key_name()
        for name in sorted(keys):
            marker = "*" if name == active_name else " "
            print(f"{marker} {name}")
    elif args.command == "switch":
        name, _ = switch_active_key(args.name)
        print(f"已切换到 Gemini key: {name}")
