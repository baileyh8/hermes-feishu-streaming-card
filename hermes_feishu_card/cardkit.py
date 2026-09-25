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
import logging
import time
from typing import Any
from urllib.parse import quote

from .card_limits import serialize_card_for_delivery


logger = logging.getLogger(__name__)

MAX_ENTITIES = 4096
ENTITY_RETENTION_SECONDS = 7200.0
STREAM_LIFETIME_SECONDS = 540.0
MIN_MUTATION_INTERVAL = 0.125


def _normalize_element_ids(card: dict[str, Any]) -> dict[str, Any]:
    """Keep CardKit IDs globally unique and within its 20-character API limit.

    Feishu rejects distinct long timeline IDs with 300301. Normalize before
    both entity creation and diffing, so incremental updates address the same
    short IDs as full replacements. Never change the caller's render snapshot.
    """
    result = deepcopy(card)
    reserved: set[str] = set()
    def reserve(node: Any) -> None:
        if isinstance(node, dict):
            value = node.get('element_id')
            if isinstance(value, str) and 1 <= len(value) <= 20:
                reserved.add(value)
            for value in node.values():
                reserve(value)
        elif isinstance(node, list):
            for value in node:
                reserve(value)
    reserve(result)
    seen: set[str] = set()
    def visit(node: Any, path: str) -> None:
        if isinstance(node, dict):
            value = node.get('element_id')
            if isinstance(value, str):
                candidate = value
                if not 1 <= len(value) <= 20 or value in seen:
                    salt = 0
                    while True:
                        candidate = 'hfc_' + sha256(f'{value}:{path}:{salt}'.encode()).hexdigest()[:16]
                        if candidate not in seen and candidate not in reserved:
                            break
                        salt += 1
                node['element_id'] = candidate
                seen.add(candidate)
            for key, value in node.items():
                visit(value, f'{path}/{key}')
        elif isinstance(node, list):
            for index, value in enumerate(node):
                visit(value, f'{path}/{index}')
    visit(result, '')
    return result


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
        # Message ids whose recovery already failed (no card reference in the
        # message body): skip the lookup GET on every subsequent update.
        self._unrecoverable: set[str] = set()
        # FeishuClient is also constructed by synchronous CLI/maintenance code.
        # Python 3.9 binds Lock eagerly, so create it only on first async send.
        self.creation_lock: asyncio.Lock | None = None

    def _prune(self) -> None:
        now = time.monotonic()
        expired = {id(e) for e in self.deliveries.values()
                   if not e.lock.locked() and now - e.touched_at > ENTITY_RETENTION_SECONDS}
        self.deliveries = {k: e for k, e in self.deliveries.items() if id(e) not in expired}
        self.entities = {k: e for k, e in self.entities.items() if id(e) not in expired}

    async def recover_from_message(self, message_id: str) -> bool:
        """Rebuild the in-memory entity for a cardkit card after a restart.

        ``send`` stores the created card entity keyed by message_id, but that
        mapping lives only in process memory. After a sidecar restart (or
        entity eviction) ``update`` misses and the plain-JSON IM PATCH fallback
        fails with 400/230011 because the message is a cardkit card. The
        original send delivered the card as a reference
        ``{"type":"card","data":{"card_id":...}}``, so the card_id can be read
        back from the message body and the entity rebuilt for future updates.
        """
        if message_id in self.entities:
            return True
        if message_id in self._unrecoverable:
            return False
        if len(self.entities) >= MAX_ENTITIES:
            return False
        from .feishu_client import FeishuAPIError
        try:
            payload = await self.client._request_json(
                'GET', f'/im/v1/messages/{quote(message_id, safe="")}',
                token=await self.client._tenant_token(),
            )
        except FeishuAPIError:
            # Transient failures (network, token) stay retryable; only a
            # successful lookup without a card reference is permanent.
            return False
        items = (payload.get('data') or {}).get('items')
        body = items[0].get('body', {}).get('content', '') if items else ''
        try:
            content = json.loads(body) if isinstance(body, str) else None
        except (json.JSONDecodeError, TypeError):
            content = None
        card_id = None
        if isinstance(content, dict):
            data = content.get('data')
            if content.get('type') == 'card' and isinstance(data, dict):
                candidate = data.get('card_id')
                if isinstance(candidate, str) and candidate.strip():
                    card_id = candidate.strip()
        if card_id is None:
            self._unrecoverable.add(message_id)
            return False
        entity = Entity(card_id=card_id, card={}, created_at=time.monotonic())
        entity.streaming = False
        entity.last_full_update = 0.0
        self.entities[message_id] = entity
        self.deliveries[card_id] = entity
        logger.info('CardKit entity recovered from message: entity_hash=%s',
                    sha256(card_id.encode()).hexdigest()[:12])
        return True

    async def send(self, chat_id: str, card: dict[str, Any], **kwargs: Any) -> Any:
        from .feishu_client import FeishuAPIError
        card = _normalize_element_ids(card)
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
        from .feishu_client import FeishuAPIError
        try:
            await self.client._request_json(
                method, f'/cardkit/v1/cards/{quote(entity.card_id, safe="")}{suffix}',
                token=token, json_body=body,
            )
        except FeishuAPIError as exc:
            # Correlate failing payloads without logging conversation text or IDs.
            payload = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
            code = str(exc.api_code or '')
            logger.warning(
                'CardKit mutation failed: entity_hash=%s payload_sha256=%s bytes=%s '
                'sequence=%s operation=%s api_code=%s',
                sha256(entity.card_id.encode()).hexdigest()[:12], sha256(payload).hexdigest(),
                len(payload), entity.sequence,
                'content' if suffix.endswith('/content') else ('settings' if suffix else 'full'),
                code if code.isdigit() and len(code) <= 10 else 'unknown',
            )
            raise
        entity.touched_at = time.monotonic()

    async def update(self, message_id: str, card: dict[str, Any]) -> bool:
        entity = self.entities.get(message_id)
        if entity is None:
            return False
        serialize_card_for_delivery(card)
        async with entity.lock:
            final = _normalize_element_ids(card)
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
