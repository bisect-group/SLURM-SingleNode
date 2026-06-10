from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


try:  # pragma: no cover - exercised only when PyYAML is installed.
    import yaml as _yaml
except ModuleNotFoundError:  # pragma: no cover - fallback is covered.
    _yaml = None


def load_yaml(path: str | Path) -> Any:
    text = Path(path).read_text()
    if _yaml is not None:
        return _yaml.safe_load(text)
    return loads(text)


def dump_yaml(data: Any) -> str:
    if _yaml is not None:
        return _yaml.safe_dump(data, sort_keys=False)
    return dumps(data)


def loads(text: str) -> Any:
    lines = _clean_lines(text)
    if not lines:
        return None
    value, index = _parse_block(lines, 0, lines[0][0])
    if index != len(lines):
        raise ValueError(f"unexpected trailing YAML at line {lines[index][2]}")
    return value


def dumps(data: Any, indent: int = 0) -> str:
    return "\n".join(_dump_lines(data, indent)) + "\n"


def _clean_lines(text: str) -> list[tuple[int, str, int]]:
    cleaned: list[tuple[int, str, int]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        content = _strip_comment(raw.rstrip())
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip(" "))
        if indent % 2 != 0:
            raise ValueError(f"indent must use multiples of two spaces at line {lineno}")
        cleaned.append((indent, content.strip(), lineno))
    return cleaned


def _strip_comment(raw: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in ("'", '"'):
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        if char == "#" and quote is None and (index == 0 or raw[index - 1].isspace()):
            return raw[:index].rstrip()
    return raw


def _parse_block(
    lines: list[tuple[int, str, int]], index: int, indent: int
) -> tuple[Any, int]:
    if index >= len(lines):
        return None, index
    current_indent, current, _ = lines[index]
    if current_indent < indent:
        return None, index
    if current_indent != indent:
        raise ValueError(f"unexpected indent at line {lines[index][2]}")
    if current.startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_map(lines, index, indent)


def _parse_map(
    lines: list[tuple[int, str, int]], index: int, indent: int
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        line_indent, content, lineno = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise ValueError(f"unexpected nested mapping line {lineno}")
        if content.startswith("- "):
            break
        key, sep, rest = content.partition(":")
        if not sep:
            raise ValueError(f"expected key: value at line {lineno}")
        key = _parse_key(key.strip())
        rest = rest.strip()
        index += 1
        if rest:
            result[key] = _parse_scalar(rest)
            continue
        if index >= len(lines) or lines[index][0] <= indent:
            result[key] = None
            continue
        value, index = _parse_block(lines, index, lines[index][0])
        result[key] = value
    return result, index


def _parse_list(
    lines: list[tuple[int, str, int]], index: int, indent: int
) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        line_indent, content, lineno = lines[index]
        if line_indent < indent:
            break
        if line_indent != indent or not content.startswith("- "):
            break
        rest = content[2:].strip()
        index += 1
        if rest:
            if ":" in rest and not rest.startswith(("'", '"')):
                key, _, value = rest.partition(":")
                item: dict[str, Any] = {_parse_key(key.strip()): _parse_scalar(value.strip())}
                if index < len(lines) and lines[index][0] > indent:
                    nested, index = _parse_block(lines, index, lines[index][0])
                    if isinstance(nested, dict):
                        item.update(nested)
                    else:
                        raise ValueError(f"list item mapping expected at line {lineno}")
                result.append(item)
            else:
                result.append(_parse_scalar(rest))
            continue
        if index >= len(lines) or lines[index][0] <= indent:
            result.append(None)
            continue
        value, index = _parse_block(lines, index, lines[index][0])
        result.append(value)
    return result, index


def _parse_key(raw: str) -> str:
    if raw.startswith(("'", '"')):
        return str(ast.literal_eval(raw))
    return raw


def _parse_scalar(raw: str) -> Any:
    if raw == "":
        return ""
    lowered = raw.lower()
    if lowered in {"null", "~"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if raw.startswith(("'", '"')):
        return ast.literal_eval(raw)
    if raw == "{}":
        return {}
    if raw == "[]":
        return []
    if raw.startswith("[") or raw.startswith("{"):
        raise ValueError("non-empty inline YAML collections require PyYAML")
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def _dump_lines(data: Any, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(data, dict):
        if not data:
            return [f"{prefix}{{}}"]
        lines: list[str] = []
        for key, value in data.items():
            key_s = _dump_key(str(key))
            if value == {}:
                lines.append(f"{prefix}{key_s}: {{}}")
                continue
            if value == []:
                lines.append(f"{prefix}{key_s}: []")
                continue
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{key_s}:")
                lines.extend(_dump_lines(value, indent + 2))
            else:
                lines.append(f"{prefix}{key_s}: {_dump_scalar(value)}")
        return lines
    if isinstance(data, list):
        if not data:
            return [f"{prefix}[]"]
        lines = []
        for value in data:
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(_dump_lines(value, indent + 2))
            else:
                lines.append(f"{prefix}- {_dump_scalar(value)}")
        return lines
    return [f"{prefix}{_dump_scalar(data)}"]


def _dump_key(key: str) -> str:
    if not key or any(c.isspace() for c in key) or key[0] in "-'\"{}[]":
        return repr(key)
    return key


def _dump_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or text.lower() in {"null", "true", "false"} or text.startswith((" ", "-", "{", "[", "#")):
        return repr(text)
    if ": " in text or " #" in text:
        return repr(text)
    return text
