"""Ordered terminal delivery, independent of HTTP and application storage.

The coordinator freezes routing while accepting the terminal event. Optional
presentation enrichment happens before constructing the plan; it cannot change
the accepted disposition. This executor never chooses a new route or native
fallback after Hermes has transferred ownership.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .delivery import CardDeliveryResult


@dataclass(frozen=True, repr=False)
class TerminalTarget:
    session_key: str
    message_id: str
    chat_id: str
    bot_id: str | None
    thread_id: str | None
    reply_to_message_id: str | None
    reply_in_thread: bool
    profile_id: str | None
    event: str
    sequence: int

    @property
    def recovery_key(self) -> str:
        return f"{self.session_key}:{self.message_id}:{self.sequence}"


@dataclass(frozen=True, repr=False)
class TerminalDeliveryPlan:
    target: TerminalTarget
    disposition: str
    card: dict[str, Any] = field(repr=False)
    allow_recover: bool = True
    resume_recovery: bool = False


class TerminalOwner(Protocol):
    terminal_delivery_state: str


class TerminalTransport(Protocol):
    async def patch(
        self, plan: TerminalDeliveryPlan, *, is_current: Callable[[], bool]
    ) -> bool: ...

    async def retry(
        self, plan: TerminalDeliveryPlan, *, is_current: Callable[[], bool]
    ) -> bool: ...

    async def recover(self, plan: TerminalDeliveryPlan) -> CardDeliveryResult: ...


@dataclass(frozen=True)
class TerminalDeliveryOutcome:
    updated: bool
    commit_native: bool = False
    recovery: CardDeliveryResult | None = None
    superseded: bool = False


async def deliver_terminal(
    plan: TerminalDeliveryPlan,
    owner: TerminalOwner,
    transport: TerminalTransport,
    *,
    is_current: Callable[[], bool],
) -> TerminalDeliveryOutcome:
    """PATCH, retry, then recover once using the accepted delivery identity.

    The owner is the accepted turn object, never a lookup by a reusable session
    key. The coordinator retains checkpoint, receipt and native-ledger commits.
    After a restart a pending recovery create can resume by the same UUID.
    Unknown recovery remains unknown; it must not trigger a native second send.
    """
    if not is_current():
        return TerminalDeliveryOutcome(False, superseded=True)
    updated = False
    if not plan.resume_recovery:
        updated = await transport.patch(plan, is_current=is_current)
        if not is_current():
            return TerminalDeliveryOutcome(False, superseded=True)
        if not updated:
            updated = await transport.retry(plan, is_current=is_current)
            if not is_current():
                return TerminalDeliveryOutcome(False, superseded=True)
    recovery = None
    if plan.disposition in {"card", "segmented"}:
        if updated:
            owner.terminal_delivery_state = "delivered"
        elif (
            plan.disposition == "card"
            and plan.allow_recover
            and (owner.terminal_delivery_state == "pending" or plan.resume_recovery)
        ):
            owner.terminal_delivery_state = "recovering"
            recovery = await transport.recover(plan)
            if not is_current():
                return TerminalDeliveryOutcome(False, recovery=recovery, superseded=True)
            updated = recovery.delivered
            if updated:
                owner.terminal_delivery_state = "recovered"
            elif plan.resume_recovery or recovery.retry_count or recovery.outcome != "not_sent":
                # A refusal of the repeat cannot rule out an earlier write.
                owner.terminal_delivery_state = "unknown"
            else:
                owner.terminal_delivery_state = "not_sent"
        elif plan.disposition == "card" and not plan.allow_recover and not plan.resume_recovery:
            owner.terminal_delivery_state = "failed"
        else:
            updated = owner.terminal_delivery_state in {"delivered", "recovered"}
    return TerminalDeliveryOutcome(
        updated=updated,
        commit_native=updated and plan.disposition == "native",
        recovery=recovery,
    )
