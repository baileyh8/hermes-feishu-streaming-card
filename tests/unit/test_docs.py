from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_doc(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_readme_documents_sidecar_only_and_supported_hermes_version():
    readme = read_doc("README.md")

    assert "sidecar-only" in readme.lower()
    assert "v2026.4.23" in readme


def test_event_protocol_documents_card_status_labels():
    event_protocol = read_doc("docs/event-protocol.md")

    assert "思考中" in event_protocol
    assert "已完成" in event_protocol
