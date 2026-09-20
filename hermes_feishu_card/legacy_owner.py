"""Static, token-free receipts for the rare interaction-first message owner."""
from __future__ import annotations

from copy import deepcopy

from .card_limits import inspect_card_limits
from .text import split_markdown_blocks


def static_legacy_receipt(card):
    """Keep only rendered text, never callback controls or nested values."""
    title = card.get("header", {}).get("title", {}).get("content", "Hermes Agent")
    if not isinstance(title, str):
        title = "Hermes Agent"
    elements = []
    for element in card.get("elements", []):
        if element.get("tag") == "markdown" and isinstance(element.get("content"), str):
            elements.append({"tag": "markdown", "content": element["content"]})
        elif element.get("tag") == "hr":
            elements.append({"tag": "hr"})
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": title}},
        "elements": elements,
    }


def valid_legacy_receipt(card):
    if card == {}:
        return True
    if not isinstance(card, dict):
        return False
    try:
        return static_legacy_receipt(card) == card and inspect_card_limits(card).safe
    except (AttributeError, TypeError, ValueError):
        return False


def legacy_owner_body(receipt, source_card, primary_text=None):
    """Retain the question/decision and add an answer in the same dialect.

    Tables remain full Markdown text on this fallback rail. Every returned
    card is inspected by the caller; no answer is shortened to make it fit.
    """
    card = deepcopy(receipt)
    elements = card["elements"]
    elements.append({"tag": "hr"})
    if primary_text:
        elements.extend({"tag": "markdown", "content": chunk}
                        for chunk in split_markdown_blocks(primary_text, 2400))
    for element in source_card.get("body", {}).get("elements", []):
        if element.get("tag") != "markdown" or not isinstance(element.get("content"), str):
            continue
        identifier = element.get("element_id", "")
        if primary_text is not None and identifier.startswith("main_content"):
            continue
        elements.append({"tag": "markdown", "content": element["content"]})
    return card
