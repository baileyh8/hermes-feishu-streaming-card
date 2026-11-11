"""Dialect and wire-budget projection for an already delivered legacy owner.

The coordinator supplies this turn's live card and title lazily. This module
never reads a session, application registry or configuration and never commits
an owner change or delivery. The result retains the original overflow evidence.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any, Callable

from .card_limits import inspect_card_limits
from .legacy_owner import legacy_owner_body
from .render import CardRenderResult, _render_limit_handoff_card


def project_existing_owner(
    result: CardRenderResult,
    *,
    receipt: dict[str, Any],
    terminal: bool,
    live_card: Callable[[], dict[str, Any] | None],
    title: Callable[[], str],
) -> CardRenderResult:
    """Keep live inputs first, otherwise retain full scope in its old dialect.

    Lazy inputs preserve the existing short-circuit order: ordinary cards need
    neither supplier; a safe live interaction does not need the fallback title.
    Suppliers only adapt the coordinator's current identity and render context.
    """
    if not receipt or result.card.get("schema") != "2.0":
        return result
    live = live_card()
    if live is not None:
        inspection = inspect_card_limits(live)
        if inspection.safe:
            return replace(result, card=live, disposition="card", inspection=inspection, limit_reason="", segments=())
    if result.disposition == "segmented":
        # Segmented continuation cards require the schema-2.0 dialect; a
        # legacy owner keeps the single-card native limit handoff.
        placeholder = _render_limit_handoff_card(title=title(), terminal=terminal)
        bounded = legacy_owner_body(receipt, placeholder)
        if not inspect_card_limits(bounded).safe:
            # Do not trim a question or accepted decision to fit the new answer.
            bounded = deepcopy(receipt)
        return replace(
            result,
            card=bounded,
            segments=(),
            disposition="native" if terminal else "deferred_native",
        )
    fallback_title = title()
    primary = result.primary_text if result.disposition == "card" else None
    card = legacy_owner_body(receipt, result.card, primary)
    inspection = inspect_card_limits(card)
    if inspection.safe:
        return replace(result, card=card, inspection=inspection)
    placeholder = _render_limit_handoff_card(title=fallback_title, terminal=terminal)
    bounded = legacy_owner_body(receipt, placeholder)
    if not inspect_card_limits(bounded).safe:
        # Do not trim a question or accepted decision to fit the new answer.
        bounded = deepcopy(receipt)
    return replace(result, card=bounded, disposition="native" if terminal else "deferred_native",
                   inspection=inspection, limit_reason=inspection.primary_reason)
