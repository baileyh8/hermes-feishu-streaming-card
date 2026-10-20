"""Only the two confirmed, complete native compression templates are disposable."""
import hashlib
import json
from pathlib import Path

import pytest

from hermes_feishu_card.transient_notices import transient_notice_recall_seconds


FIXTURE = json.loads((Path(__file__).parents[1] / 'fixtures/compression_status_e06df9c.json').read_text())


def templates():
    namespace = {}
    for part in FIXTURE['files']['agent/conversation_compression.py']['fragments']:
        assert hashlib.sha256(part['source'].encode()).hexdigest() == part['sha256']
        if part['name'].isupper():
            exec(part['source'], namespace)
    return namespace


@pytest.mark.parametrize('tokens,threshold', [(0, 0), (999, 100), (1000, 1000), (1234567, 100000)])
def test_native_preflight_template_has_existing_15_second_delay(tokens, threshold):
    text = templates()['PREFLIGHT_COMPRESSION_STATUS_TEMPLATE'].format(tokens=tokens, threshold=threshold)
    assert transient_notice_recall_seconds(text) == 15.0


def test_native_done_template_has_existing_15_second_delay():
    assert transient_notice_recall_seconds(templates()['COMPACTION_DONE_STATUS']) == 15.0


@pytest.mark.parametrize('text', [
    '🗜️ Compacting context — summarizing earlier conversation so I can continue...',
    '🗜️ Compacting context — still summarizing earlier conversation so I can continue...',
    '⏳ Working — 12 min',
    '📦 Pre-API compression: ~120,000 tokens near the context/output limit. Compacting before the next model call.',
    '⚠ Context is over the compression threshold (~120,000 tokens >= 100,000) but compression is currently blocked (cooldown).',
    '📦 Preflight compression: ~120000 tokens >= 100,000 threshold. This may take a moment.',
    '📦 Preflight compression: ~01 tokens >= 100 threshold. This may take a moment.',
    '📦 Preflight compression: ~1,00 tokens >= 100 threshold. This may take a moment.',
    '📦 Preflight compression: ~١٠٠ tokens >= 100 threshold. This may take a moment.',
    '📦 Preflight compression: ~-1 tokens >= 100 threshold. This may take a moment.',
    '📦 Preflight compression: ~100 tokens >= 100 threshold. This may take a moment. /stop for help',
    '✓ Context compaction complete — continuing turn... Please run /new',
    '✓ Context compaction complete — continuing turn…',
    '✓ 上下文压缩完成 — 继续当前轮次...',
    '> ✓ Context compaction complete — continuing turn...',
    '✓ Context compaction complete — continuing turn...\nKeep this instruction.',
])
def test_progress_failure_unknown_and_added_guidance_stay_visible(text):
    assert transient_notice_recall_seconds(text) is None
