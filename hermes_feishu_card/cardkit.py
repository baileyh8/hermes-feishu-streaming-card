"""CardKit v1 entities, with one ordered mutation stream per visible card.

REST contracts follow the official lark-oapi CardKit v1 models. IM delivery
still uses FeishuClient's UUID-bound create/reply path; entity updates never
fall back to an independent IM send.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
import json
import time
from typing import Any
from urllib.parse import quote

from .card_limits import serialize_card_for_delivery


MAX_ENTITIES = 4096
ENTITY_RETENTION_SECONDS = 7200.0
STREAM_LIFETIME_SECONDS = 540.0
MIN_MUTATION_INTERVAL = 0.125


def _text_partition(card: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    structure = deepcopy(card)
    texts: dict[str, str] = {}

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if (node.get('tag') == 'markdown' and isinstance(node.get('element_id'), str)
                    and 1 <= len(node['element_id']) <= 20):
                content = node.get('content')
                if isinstance(content, str):
                    if node['element_id'] != 'footer':
                        texts[node['element_id']] = content
                    node['content'] = ''
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(structure)
    config = structure.get('config', {})
    if isinstance(config.get('summary'), dict):
        config['summary']['content'] = ''
    return structure, texts


@dataclass
class Entity:
    card_id: str
    card: dict[str, Any]
    created_at: float
    sequence: int = 0
    streaming: bool = True
    touched_at: float = field(default_factory=time.monotonic)
    last_full_update: float = field(default_factory=time.monotonic)
    last_mutation: float | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class CardKitTransport:
    def __init__(self, client: Any):
        self.client = client
        self.entities: dict[str, Entity] = {}
        self.deliveries: dict[str, Entity] = {}
        # FeishuClient is also constructed by synchronous CLI/maintenance code.
        # Python 3.9 binds Lock eagerly, so create it only on first async send.
        self.creation_lock: asyncio.Lock | None = None

    def _prune(self) -> None:
        now = time.monotonic()
        expired = {id(e) for e in self.deliveries.values()
                   if not e.lock.locked() and now - e.touched_at > ENTITY_RETENTION_SECONDS}
        self.deliveries = {k: e for k, e in self.deliveries.items() if id(e) not in expired}
        self.entities = {k: e for k, e in self.entities.items() if id(e) not in expired}

    async def send(self, chat_id: str, card: dict[str, Any], **kwargs: Any) -> Any:
        from .feishu_client import FeishuAPIError
        serialized = serialize_card_for_delivery(card)
        delivery_uuid = kwargs.get('delivery_uuid')
        # Scope idempotency to the delivery, not changing spinner/content snapshots.
        # Reusing an IM UUID with a new entity would update an invisible card.
        raw_key = json.dumps([chat_id, kwargs.get('thread_id'), kwargs.get('reply_to_message_id'),
                              kwargs.get('reply_in_thread'), delivery_uuid], ensure_ascii=False)
        key = sha256(raw_key.encode()).hexdigest() if delivery_uuid else ''
        if self.creation_lock is None:
            self.creation_lock = asyncio.Lock()
        async with self.creation_lock:
            self._prune()
            entity = self.deliveries.get(key) if key else None
            if entity is None:
                if len(self.deliveries) >= MAX_ENTITIES:
                    raise FeishuAPIError('CardKit entity capacity reached', outcome='not_sent')
                try:
                    token = await self.client._tenant_token()
                    result = await self.client._request_json(
                        'POST', '/cardkit/v1/cards', token=token,
                        json_body={'type': 'card_json', 'data': serialized},
                    )
                except FeishuAPIError as exc:
                    # Even an ambiguous entity creation cannot have delivered
                    # an IM message: the send/reply call has not started yet.
                    raise FeishuAPIError(
                        'CardKit entity creation failed', status_code=exc.status_code,
                        api_code=exc.api_code, retryable=exc.retryable,
                        outcome='not_sent', retry_after_seconds=exc.retry_after_seconds,
                    ) from exc
                card_id = (result.get('data') or {}).get('card_id')
                if not isinstance(card_id, str) or not card_id.strip():
                    raise FeishuAPIError('CardKit create response missing card_id', outcome='not_sent')
                entity = Entity(card_id=card_id, card=deepcopy(card), created_at=time.monotonic())
                self.deliveries[key or card_id] = entity
        reference = {'type': 'card', 'data': {'card_id': entity.card_id}}
        result = await self.client.send_card_delivery(chat_id, reference, **kwargs)
        self.entities[result.message_id] = entity
        entity.touched_at = time.monotonic()
        return result

    async def _mutate(self, entity: Entity, method: str, suffix: str,
                      body: dict[str, Any]) -> None:
        now = time.monotonic()
        if entity.last_mutation is not None:
            delay = MIN_MUTATION_INTERVAL - (now - entity.last_mutation)
            if delay > 0:
                await asyncio.sleep(delay)
        entity.sequence += 1
        entity.last_mutation = time.monotonic()
        body = {**body, 'sequence': entity.sequence,
                'uuid': sha256(f'{entity.card_id}:{entity.sequence}'.encode()).hexdigest()[:40]}
        token = await self.client._tenant_token()
        await self.client._request_json(
            method, f'/cardkit/v1/cards/{quote(entity.card_id, safe="")}{suffix}',
            token=token, json_body=body,
        )
        entity.touched_at = time.monotonic()

    async def update(self, message_id: str, card: dict[str, Any]) -> bool:
        entity = self.entities.get(message_id)
        if entity is None:
            return False
        serialize_card_for_delivery(card)
        async with entity.lock:
            final = deepcopy(card)
            config = final.setdefault('config', {})
            requested_streaming = config.get('streaming_mode') is True
            if entity.streaming and (
                not requested_streaming
                or time.monotonic() - entity.created_at >= STREAM_LIFETIME_SECONDS
            ):
                # Full-card update alone does not reliably stop the client-side
                # loading state; explicitly close before publishing final content.
                await self._mutate(entity, 'PATCH', '/settings', {
                    'settings': json.dumps({'config': {'streaming_mode': False}}, ensure_ascii=False),
                })
                entity.streaming = False
            config['streaming_mode'] = entity.streaming and requested_streaming
            previous_structure, previous_text = _text_partition(entity.card)
            structure, text = _text_partition(final)
            full_update = (not entity.streaming or structure != previous_structure
                           or any(not content and previous_text.get(key) for key, content in text.items())
                           or time.monotonic() - entity.last_full_update >= 2.0)
            if full_update:
                await self._mutate(entity, 'PUT', '', {
                    'card': {'type': 'card_json', 'data': serialize_card_for_delivery(final)},
                })
                entity.last_full_update = time.monotonic()
                entity.card = final
            else:
                for element_id, content in text.items():
                    if previous_text.get(element_id) != content:
                        await self._mutate(entity, 'PUT', f'/elements/{quote(element_id, safe="")}/content', {
                            'content': content,
                        })
                # Footer/summary refresh is intentionally periodic, not a full
                # replacement on every token. The terminal path always flushes.
                entity.card = final
            return True
