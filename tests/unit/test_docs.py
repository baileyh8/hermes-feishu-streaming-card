from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_doc(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_readme_documents_sidecar_only_and_supported_hermes_version():
    readme = read_doc("README.md")

    assert "sidecar-only" in readme.lower()
    assert "v2026.4.23" in readme


def test_mainline_docs_mark_legacy_dual_as_not_active_runtime():
    docs = "\n".join(
        [
            read_doc("README.md"),
            read_doc("TODO.md"),
            read_doc("docs/architecture.md"),
        ]
    ).lower()

    assert "legacy" in docs
    assert "dual" in docs
    assert "not active runtime" in docs or "不是 active runtime" in docs


def test_event_protocol_documents_card_status_labels():
    event_protocol = read_doc("docs/event-protocol.md")

    assert "思考中" in event_protocol
    assert "已完成" in event_protocol


def test_docs_describe_event_forwarding_but_not_cardkit_completion():
    docs = "\n".join(
        [
            read_doc("README.md"),
            read_doc("docs/architecture.md"),
            read_doc("TODO.md"),
        ]
    )

    assert "Hermes hook" in docs or "Hermes 到 sidecar" in docs
    assert "Feishu CardKit" in docs
    assert "仍未完成" in docs or "后续阶段" in docs
