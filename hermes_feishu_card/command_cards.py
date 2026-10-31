"""Pure command card and picker presentation extracted from runtime hooks.

Hermes still owns the command catalog and its handlers. These builders consume
already selected options and never register callbacks or execute a command.
"""
from __future__ import annotations

import json
import re
from typing import Any


def model_picker_options(
    providers: Any,
    *,
    current_model: str = "",
    max_options: int = 24,
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    if not isinstance(providers, list):
        return options
    current = str(current_model or "").strip()
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        provider_slug = str(provider.get("slug") or provider.get("provider") or "").strip()
        provider_name = str(provider.get("name") or provider_slug or "provider").strip()
        models = provider.get("models")
        if not isinstance(models, list):
            continue
        for model in models:
            model_id = str(model or "").strip()
            if not model_id:
                continue
            label = f"{provider_name} · {model_id}"
            if model_id == current:
                label = f"当前 · {label}"
            options.append(
                {
                    "label": label[:80],
                    "value": json.dumps(
                        {"provider": provider_slug, "model": model_id},
                        ensure_ascii=False,
                    ),
                    "style": "primary" if model_id == current else "default",
                }
            )
            if len(options) >= max_options:
                return options
    return options


def model_picker_provider_tree(providers: Any) -> list[dict[str, Any]]:
    """Return the public provider/model tree already selected by Hermes."""
    if not isinstance(providers, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen_providers: set[str] = set()
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        provider_slug = str(
            provider.get("slug") or provider.get("provider") or ""
        ).strip()
        provider_key = provider_slug.casefold()
        models = provider.get("models")
        if not provider_slug or provider_key in seen_providers or not isinstance(models, list):
            continue
        model_ids: list[str] = []
        seen_models: set[str] = set()
        for model in models:
            model_id = str(model or "").strip()
            if not model_id or model_id in seen_models:
                continue
            seen_models.add(model_id)
            model_ids.append(model_id)
        if not model_ids:
            continue
        try:
            total_models = int(provider.get("total_models", len(model_ids)))
        except (TypeError, ValueError):
            total_models = len(model_ids)
        total_models = max(len(model_ids), total_models)
        seen_providers.add(provider_key)
        normalized.append(
            {
                "slug": provider_slug,
                "name": str(provider.get("name") or provider_slug).strip()
                or provider_slug,
                "models": model_ids,
                "total_models": total_models,
                "is_current": bool(provider.get("is_current")),
            }
        )
    return normalized


def model_picker_provider_options(
    providers: list[dict[str, Any]],
    *,
    current_provider: str,
    max_options: int = 100,
) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    current = str(current_provider or "").strip().casefold()
    for provider in providers:
        provider_slug = str(provider.get("slug") or "").strip()
        provider_name = str(provider.get("name") or provider_slug).strip()
        models = provider.get("models")
        if not provider_slug or not isinstance(models, list) or not models:
            continue
        try:
            total_models = max(len(models), int(provider.get("total_models", len(models))))
        except (TypeError, ValueError):
            total_models = len(models)
        label = f"{provider_name} ({total_models} 个模型)"
        if bool(provider.get("is_current")) or provider_slug.casefold() == current:
            label = f"当前 · {label}"
        options.append({"label": label[:80], "value": provider_slug})
        if len(options) >= max_options:
            break
    return options


def model_picker_model_options(
    provider: dict[str, Any],
    *,
    current_model: str,
    max_options: int = 100,
) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    provider_slug = str(provider.get("slug") or "").strip()
    models = provider.get("models")
    if not provider_slug or not isinstance(models, list):
        return options
    current = str(current_model or "").strip()
    for model in models:
        model_id = str(model or "").strip()
        if not model_id:
            continue
        label = f"当前 · {model_id}" if model_id == current else model_id
        options.append(
            {
                "label": label[:80],
                "value": json.dumps(
                    {"provider": provider_slug, "model": model_id},
                    ensure_ascii=False,
                ),
            }
        )
        if len(options) >= max_options:
            break
    return options


def model_picker_provider(
    providers: list[dict[str, Any]], provider_slug: str
) -> dict[str, Any] | None:
    selected = str(provider_slug or "").strip().casefold()
    for provider in providers:
        if str(provider.get("slug") or "").strip().casefold() == selected:
            return provider
    return None


def model_picker_current_provider_slug(
    providers: list[dict[str, Any]], current_provider: str
) -> str:
    for provider in providers:
        if bool(provider.get("is_current")):
            return str(provider.get("slug") or "").strip()
    current = str(current_provider or "").strip().casefold()
    for provider in providers:
        provider_slug = str(provider.get("slug") or "").strip()
        if provider_slug.casefold() == current:
            return provider_slug
    return ""


def hfc_native_model_picker_card(
    *,
    picker_id: str,
    providers: list[dict[str, Any]],
    current_provider: str,
    current_model: str,
    selected_provider: str = "",
) -> dict[str, Any]:
    provider = model_picker_provider(providers, selected_provider)
    if provider is None:
        options = model_picker_provider_options(
            providers,
            current_provider=current_provider,
        )
        description_parts = []
        if current_model:
            description_parts.append(f"当前模型：`{current_model}`")
        if current_provider:
            description_parts.append(f"当前 Provider：`{current_provider}`")
        description_parts.append("先选择 Provider，再选择模型。")
        elements: list[dict[str, Any]] = [
            {"tag": "markdown", "content": "\n".join(description_parts)},
            {
                "tag": "action",
                "actions": [
                    hfc_select_static(
                        placeholder="选择 Provider",
                        value={
                            "hfc_action": "model_picker",
                            "hfc_model_picker_id": picker_id,
                            "hfc_model_picker_view": "providers",
                        },
                        options=options,
                    )
                ],
            },
            {
                "tag": "action",
                "actions": [
                    hfc_button(
                        "取消",
                        {
                            "hfc_action": "model_picker",
                            "hfc_model_picker_id": picker_id,
                            "hfc_model_picker_nav": "cancel",
                        },
                    )
                ],
            },
        ]
        title = "选择模型"
    else:
        provider_slug = str(provider.get("slug") or "").strip()
        provider_name = str(provider.get("name") or provider_slug).strip()
        options = model_picker_model_options(
            provider,
            current_model=current_model,
        )
        initial_option = ""
        if provider_slug.casefold() == str(current_provider or "").strip().casefold():
            initial_option = next(
                (
                    option["value"]
                    for option in options
                    if json.loads(option["value"]).get("model") == current_model
                ),
                "",
            )
        elements = [
            {
                "tag": "markdown",
                "content": f"Provider：`{provider_name}`\n\n请选择模型。",
            },
            {
                "tag": "action",
                "actions": [
                    hfc_select_static(
                        placeholder="选择模型",
                        value={
                            "hfc_action": "model_picker",
                            "hfc_model_picker_id": picker_id,
                            "hfc_model_picker_view": "models",
                        },
                        options=options,
                        initial_option=initial_option,
                    )
                ],
            },
            {
                "tag": "action",
                "actions": [
                    hfc_button(
                        "返回",
                        {
                            "hfc_action": "model_picker",
                            "hfc_model_picker_id": picker_id,
                            "hfc_model_picker_nav": "back",
                        },
                    ),
                    hfc_button(
                        "取消",
                        {
                            "hfc_action": "model_picker",
                            "hfc_model_picker_id": picker_id,
                            "hfc_model_picker_nav": "cancel",
                        },
                    ),
                ],
            },
        ]
        title = f"选择模型 · {provider_name}"

    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"content": title, "tag": "plain_text"},
            "template": "blue",
        },
        "elements": elements,
    }


def resume_picker_options(
    sessions: Any,
    *,
    current_session_id: str = "",
    max_options: int = 10,
) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    if not isinstance(sessions, list):
        return options
    current = str(current_session_id or "").strip()
    for row in sessions:
        if not isinstance(row, dict):
            continue
        session_id = str(row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        if not session_id or not title:
            continue
        preview = str(row.get("preview") or "").strip()[:40]
        prefix = "当前 · " if session_id == current else ""
        suffix = f" — {preview}" if preview else ""
        label = f"{prefix}{title}{suffix}"[:80]
        options.append({"label": label, "value": session_id})
        if len(options) >= max_options:
            break
    return options


def parse_model_picker_choice(choice: str) -> tuple[str, str] | None:
    try:
        data = json.loads(choice)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    provider = str(data.get("provider") or "").strip()
    model = str(data.get("model") or "").strip()
    if not provider or not model:
        return None
    return provider, model


def hfc_slash_confirm_detail(message: str) -> str:
    text = str(message or "").strip()
    text = re.sub(r"^⚠️\s*\*\*Confirm /[^*]+\*\*\s*", "", text).strip()
    text = re.split(r"\n\s*Choose:\s*\n", text, maxsplit=1)[0].strip()
    text = re.sub(r"\n\s*_Text fallback:.*$", "", text, flags=re.DOTALL).strip()
    return text or str(message or "").strip()


def hfc_button(label: str, value: dict[str, Any], button_type: str = "default") -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": button_type,
        "value": value,
    }


def hfc_select_static(
    *,
    placeholder: str,
    value: dict[str, Any],
    options: list[dict[str, str]],
    initial_option: str = "",
) -> dict[str, Any]:
    element: dict[str, Any] = {
        "tag": "select_static",
        "placeholder": {"tag": "plain_text", "content": placeholder},
        "value": value,
        "options": [
            {
                "text": {"tag": "plain_text", "content": str(option.get("label") or "")[:80]},
                "value": str(option.get("value") or ""),
            }
            for option in options
            if option.get("label") and option.get("value")
        ],
    }
    if initial_option:
        element["initial_option"] = initial_option
    return element


def hfc_command_result_card(
    *,
    title: str,
    content: str,
    template: str = "green",
) -> dict[str, Any]:
    from .render import MAIN_CONTENT_CHUNK_CHARS
    from .text import split_markdown_blocks

    normalized_content = str(content or "").strip() or "已处理。"
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"content": title, "tag": "plain_text"},
            "template": template,
        },
        "elements": [
            {
                "tag": "markdown",
                "content": chunk,
            }
            for chunk in split_markdown_blocks(
                normalized_content,
                MAIN_CONTENT_CHUNK_CHARS,
            )
        ],
    }


def hfc_command_result_title(command: str) -> str:
    normalized = str(command or "").strip().lower()
    return {
        "new": "会话已重置",
        "reset": "会话已重置",
        "clear": "上下文已清理",
        "undo": "已撤销上一步",
        "stop": "已停止",
        "model": "模型已更新",
        "compress": "上下文压缩",
    }.get(normalized, f"/{normalized}" if normalized else "命令反馈")


def hfc_command_result_template(content: str) -> str:
    text = str(content or "").strip().lower()
    if text.startswith(("❌", "error", "failed")) or "失败" in text or "error:" in text:
        return "red"
    if text.startswith(("⏳", "正在", "running", "starting")):
        return "blue"
    if text.startswith(("⚠️", "warning")) or "cancel" in text or "取消" in text:
        return "orange"
    return "green"
