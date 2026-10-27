"""Strict, byte-preserving ownership of the official HFC config delta.

This module never accesses the filesystem. Its receipt is produced only after
the caller has verified the official before/after bytes. Unrelated YAML remains
in the caller's current bytes; unsupported or ambiguous syntax fails closed.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import copy
import re
from dataclasses import dataclass
from typing import Any

yaml = importlib.import_module("yaml")

KEY = "hermes-feishu-card"


class ConfigDeltaRefused(ValueError):
    """The YAML or the owned values cannot be established unambiguously."""


@dataclass(frozen=True)
class Document:
    text: str
    root: Any
    mapping: dict[str, Any]


def _parse(raw: bytes) -> Document:
    try:
        text = raw.decode("utf-8")
        if any(isinstance(t, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken,
                              yaml.tokens.TagToken)) for t in yaml.scan(text)):
            raise ConfigDeltaRefused("ambiguous YAML tokens")
        root = yaml.compose(text)
        parsed = yaml.safe_load(text)
    except (UnicodeError, yaml.YAMLError) as exc:
        raise ConfigDeltaRefused("invalid YAML") from exc
    if root is None:
        return Document(text, None, {})
    if not isinstance(root, yaml.MappingNode) or type(parsed) is not dict:
        raise ConfigDeltaRefused("config root is not a mapping")

    def check(node: Any) -> None:
        if isinstance(node, yaml.MappingNode):
            seen: set[str] = set()
            for key, value in node.value:
                if (not isinstance(key, yaml.ScalarNode)
                        or key.tag != "tag:yaml.org,2002:str"
                        or key.value == "<<" or key.value in seen):
                    raise ConfigDeltaRefused("ambiguous YAML mapping")
                seen.add(key.value)
                check(value)
        elif isinstance(node, yaml.SequenceNode):
            for value in node.value:
                check(value)
    check(root)
    return Document(text, root, parsed)


def _lookup(node: Any, keys: tuple[str, ...]) -> Any:
    for name in keys:
        if not isinstance(node, yaml.MappingNode):
            return None
        pair = next(((k, v) for k, v in node.value if k.value == name), None)
        if pair is None:
            return None
        node = pair[1]
    return node


def _parts(document: Document) -> tuple[list[str], list[str], dict[str, Any]]:
    plugins = document.mapping.get("plugins", {})
    if type(plugins) is not dict:
        raise ConfigDeltaRefused("plugin config is not a mapping")
    enabled, disabled = plugins.get("enabled", []), plugins.get("disabled", [])
    entries = plugins.get("entries", {})
    if (type(enabled) is not list or type(disabled) is not list
            or any(type(item) is not str for item in enabled + disabled)
            or len(enabled) != len(set(enabled)) or len(disabled) != len(set(disabled))
            or type(entries) is not dict):
        raise ConfigDeltaRefused("plugin config shape is invalid")
    entry = entries.get(KEY, {})
    if type(entry) is not dict:
        raise ConfigDeltaRefused("plugin entry is not a mapping")
    for path in (("plugins", "enabled"), ("plugins", "disabled")):
        node = _lookup(document.root, path)
        if isinstance(node, yaml.SequenceNode) and any(
                not isinstance(item, yaml.ScalarNode) or item.style in ("|", ">")
                or item.start_mark.line != item.end_mark.line for item in node.value):
            raise ConfigDeltaRefused("multiline plugin selection is ambiguous")
    override = _lookup(document.root, ("plugins", "entries", KEY, "allow_tool_override"))
    if isinstance(override, yaml.ScalarNode) and (override.style in ("|", ">")
                                                  or override.start_mark.line != override.end_mark.line):
        raise ConfigDeltaRefused("multiline plugin override is ambiguous")
    return enabled, disabled, entry


def _state(document: Document) -> tuple[bool, bool, bool, Any]:
    enabled, disabled, entry = _parts(document)
    return KEY in enabled, KEY in disabled, "allow_tool_override" in entry, entry.get("allow_tool_override")


def _same_state(first: tuple[bool, bool, bool, Any], second: tuple[bool, bool, bool, Any]) -> bool:
    return first[:3] == second[:3] and type(first[3]) is type(second[3]) and first[3] == second[3]


def validate_preimage(raw: bytes) -> None:
    document = _parse(raw)
    enabled, disabled, _entry = _parts(document)
    if KEY in enabled and KEY in disabled:
        raise ConfigDeltaRefused("conflicting plugin selection")


def _last_leaf(node: Any) -> int:
    if isinstance(node, yaml.MappingNode) and node.value:
        return max(_last_leaf(value) for _, value in node.value)
    if isinstance(node, yaml.SequenceNode) and node.value:
        return max(_last_leaf(value) for value in node.value)
    return int(node.end_mark.index)


def _line_end(text: str, index: int) -> int:
    if index and text[index - 1] == "\n":
        return index
    at = text.find("\n", index)
    return len(text) if at < 0 else at + 1


def _span(document: Document, keys: tuple[str, ...]) -> tuple[int, int] | None:
    parent = _lookup(document.root, keys[:-1]) if keys[:-1] else document.root
    if not isinstance(parent, yaml.MappingNode):
        return None
    pair = next(((k, v) for k, v in parent.value if k.value == keys[-1]), None)
    if pair is None:
        return None
    key, value = pair
    if parent.flow_style:
        raise ConfigDeltaRefused("flow mapping edit is ambiguous")
    start = document.text.rfind("\n", 0, key.start_mark.index) + 1
    if document.text[start:key.start_mark.index].strip():
        raise ConfigDeltaRefused("mapping key location is ambiguous")
    return start, _line_end(document.text, _last_leaf(value))


def _branch_hash(document: Document) -> str | None:
    span = _span(document, ("plugins",))
    if span is None:
        return None
    return hashlib.sha256(document.text[slice(*span)].encode("utf-8")).hexdigest()


def make_receipt(before: bytes, post: bytes) -> dict[str, Any]:
    """Describe only a previously verified official enable result."""
    old, new = _parse(before), _parse(post)
    before_enabled, before_disabled, _ = _parts(old)
    after_enabled, after_disabled, _ = _parts(new)
    if KEY in before_enabled and KEY in before_disabled:
        raise ConfigDeltaRefused("conflicting plugin selection")
    if not _same_state(_state(new), (True, False, True, False)):
        raise ConfigDeltaRefused("official plugin state is unexpected")
    for first, second in ((before_enabled, after_enabled), (before_disabled, after_disabled)):
        if sorted(item for item in first if item != KEY) != sorted(item for item in second if item != KEY):
            raise ConfigDeltaRefused("official plugin selection changed unrelated members")
    old_unowned, new_unowned = _unowned(old), _unowned(new)
    # The official writer may normalize only selection order and its own
    # schema stamp; these are never claimed by the delta receipt.
    for value in (old_unowned, new_unowned):
        value.pop("_config_version", None)
        plugins = value.get("plugins")
        if type(plugins) is dict:
            plugins.pop("enabled", None)
            plugins.pop("disabled", None)
            if not plugins:
                del value["plugins"]
    if not _same_value(old_unowned, new_unowned):
        raise ConfigDeltaRefused("official plugin config changed unrelated values")
    state = _state(old)
    encoded = json.dumps(state, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {"schema": 2, "before_state_sha256": hashlib.sha256(encoded).hexdigest(),
            "post_plugins_sha256": _branch_hash(new)}


def verify_receipt(before: bytes, receipt: Any) -> None:
    if (type(receipt) is not dict or set(receipt) != {"schema", "before_state_sha256", "post_plugins_sha256"}
            or type(receipt["schema"]) is not int or receipt["schema"] != 2
            or type(receipt["before_state_sha256"]) is not str
            or type(receipt["post_plugins_sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", receipt["before_state_sha256"]) is None
            or re.fullmatch(r"[0-9a-f]{64}", receipt["post_plugins_sha256"]) is None):
        raise ConfigDeltaRefused("plugin receipt is invalid")
    state = _state(_parse(before))
    encoded = json.dumps(state, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if hashlib.sha256(encoded).hexdigest() != receipt["before_state_sha256"]:
        raise ConfigDeltaRefused("plugin receipt preimage mismatch")


def classify(before: bytes, current: bytes, receipt: Any, *, allow_restored: bool = False) -> str:
    verify_receipt(before, receipt)
    old, now = _parse(before), _parse(current)
    state = _state(now)
    if _same_state(state, (True, False, True, False)):
        return "installed"
    if allow_restored and _same_state(state, _state(old)):
        return "restored"
    raise ConfigDeltaRefused("owned plugin config changed")


def _replace(text: str, start: int, end: int, replacement: str) -> str:
    return text[:start] + replacement + text[end:]


def _replace_section(text: str, start: int, end: int, replacement: str) -> str:
    if replacement and end < len(text) and not replacement.endswith(("\n", "\r")):
        newline = "\r\n" if "\r\n" in text[start:end] else "\n"
        replacement += newline
    return _replace(text, start, end, replacement)


def _unowned(document: Document) -> dict[str, Any]:
    value = copy.deepcopy(document.mapping)
    plugins = value.get("plugins")
    if type(plugins) is not dict:
        return value
    for name in ("enabled", "disabled"):
        if name in plugins:
            plugins[name] = [item for item in plugins[name] if item != KEY]
            if not plugins[name]:
                del plugins[name]
    entries = plugins.get("entries")
    if type(entries) is dict:
        entry = entries.get(KEY)
        if type(entry) is dict:
            entry.pop("allow_tool_override", None)
            if not entry:
                del entries[KEY]
        if not entries:
            del plugins["entries"]
    if not plugins:
        del value["plugins"]
    return value


def _same_value(first: Any, second: Any) -> bool:
    if type(first) is not type(second):
        return False
    if isinstance(first, dict):
        return first.keys() == second.keys() and all(_same_value(first[key], second[key]) for key in first)
    if isinstance(first, list):
        return len(first) == len(second) and all(_same_value(a, b) for a, b in zip(first, second))
    return bool(first == second)


def _assert_unowned_unchanged(current: Document, result: bytes) -> None:
    if not _same_value(_unowned(current), _unowned(_parse(result))):
        raise ConfigDeltaRefused("unowned config would change")


def _remove_pair(text: str, path: tuple[str, ...]) -> str:
    doc = _parse(text.encode())
    span = _span(doc, path)
    if span is None:
        return text
    removed = text[slice(*span)]
    # Comments within an owned block may be user edits. Do not silently remove
    # them; they require a more specific editor rather than a guessed merge.
    if "#" in removed:
        raise ConfigDeltaRefused("owned section contains comments")
    return _replace(text, *span, "")


def _set_sequence(text: str, path: tuple[str, ...], add: bool, old: Document) -> str:
    doc = _parse(text.encode())
    node = _lookup(doc.root, path)
    if not isinstance(node, yaml.SequenceNode):
        raise ConfigDeltaRefused("plugin selection missing")
    items = node.value
    matches = [i for i, item in enumerate(items) if item.value == KEY]
    if add == bool(matches):
        return text
    if any(not isinstance(item, yaml.ScalarNode) or item.tag != "tag:yaml.org,2002:str" for item in items):
        raise ConfigDeltaRefused("plugin selection is ambiguous")
    if node.flow_style:
        start, end = node.start_mark.index, node.end_mark.index
        segment = text[start:end]
        if "#" in segment:
            raise ConfigDeltaRefused("flow sequence comments are ambiguous")
        if add:
            if not items:
                return _replace(text, start, end, "[" + KEY + "]")
            original = _lookup(old.root, path)
            if not isinstance(original, yaml.SequenceNode):
                raise ConfigDeltaRefused("original selection missing")
            original_values = [item.value for item in original.value]
            old_index = original_values.index(KEY)
            following = next((name for name in original_values[old_index + 1:] if any(v.value == name for v in items)), None)
            predecessor = next((name for name in reversed(original_values[:old_index]) if any(v.value == name for v in items)), None)
            source = original.value[old_index]
            value = old.text[source.start_mark.index:source.end_mark.index]
            if following is not None:
                anchor = next(item for item in items if item.value == following)
                return _replace(text, anchor.start_mark.index, anchor.start_mark.index, value + ", ")
            if predecessor is not None:
                anchor = next(item for item in items if item.value == predecessor)
                return _replace(text, anchor.end_mark.index, anchor.end_mark.index, ", " + value)
            return _replace(text, end - 1, end - 1, ", " + value)
        index = matches[0]
        item = items[index]
        if len(items) == 1:
            return _replace(text, start, end, "[]")
        if index + 1 < len(items):
            return _replace(text, item.start_mark.index, items[index + 1].start_mark.index, "")
        return _replace(text, items[index - 1].end_mark.index, item.end_mark.index, "")
    if add:
        original = _lookup(old.root, path)
        if not isinstance(original, yaml.SequenceNode):
            raise ConfigDeltaRefused("original selection missing")
        original_values = [item.value for item in original.value]
        old_index = original_values.index(KEY)
        following = next((name for name in original_values[old_index + 1:] if any(v.value == name for v in items)), None)
        predecessor = next((name for name in reversed(original_values[:old_index]) if any(v.value == name for v in items)), None)
        if following is not None:
            anchor = next(item for item in items if item.value == following)
            point = text.rfind("\n", 0, anchor.start_mark.index) + 1
        elif predecessor is not None:
            anchor = next(item for item in items if item.value == predecessor)
            point = _line_end(text, anchor.end_mark.index)
        else:
            point = _line_end(text, items[-1].end_mark.index)
        source = original.value[old_index]
        source_line = old.text.rfind("\n", 0, source.start_mark.index) + 1
        source_end = _line_end(old.text, source.end_mark.index)
        original_line = old.text[source_line:source_end]
        prefix_end = len(original_line) - len(original_line.lstrip(" "))
        indent = " " * node.start_mark.column
        newline = "\r\n" if "\r\n" in text else "\n"
        insertion = indent + original_line[prefix_end:]
        if point and text[point - 1] not in "\r\n":
            insertion = newline + insertion
        if point < len(text) and not insertion.endswith(("\n", "\r")):
            insertion += newline
        return _replace(text, point, point, insertion)
    target = items[matches[0]]
    if len(items) == 1:
        start = node.start_mark.index
        end = target.end_mark.index
        return _replace(text, start, end, "[]")
    start = text.rfind("\n", 0, target.start_mark.index) + 1
    end = _line_end(text, target.end_mark.index)
    if "#" in text[start:end]:
        raise ConfigDeltaRefused("selection item contains comments")
    return _replace(text, start, end, "")


def restore_bytes(before: bytes, current: bytes, receipt: Any) -> bytes:
    state = classify(before, current, receipt)
    if state == "restored":
        return current
    old, now = _parse(before), _parse(current)
    old_span, current_span = _span(old, ("plugins",)), _span(now, ("plugins",))
    if current_span is not None and _branch_hash(now) == receipt["post_plugins_sha256"]:
        original = "" if old_span is None else old.text[slice(*old_span)]
        result = _replace_section(now.text, *current_span, original).encode()
        try:
            _assert_unowned_unchanged(now, result)
        except ConfigDeltaRefused:
            # The official writer may have reordered other members. Preserve
            # their current order instead of replacing the whole branch.
            pass
        else:
            return result
    text = now.text
    old_enabled, old_disabled, old_entry = _parts(old)
    if KEY not in old_enabled:
        selected = _lookup(_parse(text.encode()).root, ("plugins", "enabled"))
        if (_lookup(old.root, ("plugins", "enabled")) is None
                and isinstance(selected, yaml.SequenceNode) and len(selected.value) == 1):
            text = _remove_pair(text, ("plugins", "enabled"))
        else:
            text = _set_sequence(text, ("plugins", "enabled"), False, old)
    if KEY in old_disabled:
        text = _set_sequence(text, ("plugins", "disabled"), True, old)
    override_path = ("plugins", "entries", KEY, "allow_tool_override")
    if "allow_tool_override" not in old_entry:
        entry_path = ("plugins", "entries", KEY)
        entries_path = ("plugins", "entries")
        current_doc = _parse(text.encode())
        entry_node = _lookup(current_doc.root, entry_path)
        entries_node = _lookup(current_doc.root, entries_path)
        old_entries_node = _lookup(old.root, entries_path)
        old_entry_node = _lookup(old.root, entry_path)
        if (isinstance(old_entries_node, yaml.MappingNode) and not old_entries_node.value
                and isinstance(entries_node, yaml.MappingNode) and len(entries_node.value) == 1
                and isinstance(entry_node, yaml.MappingNode) and len(entry_node.value) == 1):
            previous_span = _span(old, entries_path)
            current_span = _span(current_doc, entries_path)
            if previous_span is None or current_span is None or "#" in text[slice(*current_span)]:
                raise ConfigDeltaRefused("plugin entries are ambiguous")
            text = _replace_section(text, *current_span, old.text[slice(*previous_span)])
        elif (isinstance(old_entry_node, yaml.MappingNode) and not old_entry_node.value
              and isinstance(entry_node, yaml.MappingNode) and len(entry_node.value) == 1):
            previous_span = _span(old, entry_path)
            current_span = _span(current_doc, entry_path)
            if previous_span is None or current_span is None or "#" in text[slice(*current_span)]:
                raise ConfigDeltaRefused("plugin entry is ambiguous")
            text = _replace_section(text, *current_span, old.text[slice(*previous_span)])
        elif (old_entries_node is None and isinstance(entries_node, yaml.MappingNode)
                and len(entries_node.value) == 1 and isinstance(entry_node, yaml.MappingNode)
                and len(entry_node.value) == 1):
            text = _remove_pair(text, entries_path)
        elif (_lookup(old.root, entry_path) is None and isinstance(entry_node, yaml.MappingNode)
              and len(entry_node.value) == 1):
            text = _remove_pair(text, entry_path)
        else:
            text = _remove_pair(text, override_path)
    elif old_entry["allow_tool_override"] is not False:
        source = _lookup(old.root, override_path)
        target = _lookup(_parse(text.encode()).root, override_path)
        if not isinstance(source, yaml.ScalarNode) or not isinstance(target, yaml.ScalarNode):
            raise ConfigDeltaRefused("original override value is ambiguous")
        text = _replace(text, target.start_mark.index, target.end_mark.index,
                        old.text[source.start_mark.index:source.end_mark.index])
    # Remove containers created by the official writer only when they are empty.
    for path in (("plugins", "entries", KEY), ("plugins", "entries")):
        current_doc = _parse(text.encode())
        old_node = _lookup(old.root, path)
        node = _lookup(current_doc.root, path)
        if old_node is None and isinstance(node, yaml.MappingNode) and not node.value:
            text = _remove_pair(text, path)
    for name in ("enabled", "disabled"):
        path = ("plugins", name)
        current_doc = _parse(text.encode())
        node = _lookup(current_doc.root, path)
        if _lookup(old.root, path) is None and isinstance(node, yaml.SequenceNode) and not node.value:
            text = _remove_pair(text, path)
    result = text.encode()
    if not _same_state(_state(_parse(result)), _state(old)):
        raise ConfigDeltaRefused("plugin delta could not be restored")
    _assert_unowned_unchanged(now, result)
    return result
