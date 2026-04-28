from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_doc(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_readme_documents_sidecar_only_and_supported_hermes_version():
    readme = read_doc("README.md")

    assert "V3.1.0" in readme
    assert "[English](README.en.md)" in readme
    assert "docs/assets/readme-cover.png" in readme
    assert "sidecar-only" in readme.lower()
    assert "setup --hermes-dir" in readme
    assert "整合安装器" in readme
    assert "streaming.enabled" in readme
    assert "display.platforms.feishu.streaming" in readme
    assert "不要把 `display.show_reasoning`" in readme
    assert "thinking.delta" in readme
    assert "v2026.4.23" in readme
    assert "Git tag `v2026.4.23+`" in readme
    assert "docs/assets/feishu-weather-card.png" in readme
    assert (ROOT / "docs/assets/readme-cover.png").exists()
    assert (ROOT / "docs/assets/feishu-weather-card.png").exists()


def test_english_readme_and_docs_are_linked():
    readme = read_doc("README.md")
    english_readme = read_doc("README.en.md")
    expected_docs = [
        "architecture",
        "event-protocol",
        "installer-safety",
        "migration",
        "e2e-verification",
        "release-readiness",
        "testing",
    ]

    assert "[中文](README.md)" in english_readme
    assert "Hermes Feishu Streaming Card Plugin V3.1.0" in english_readme
    assert "docs/assets/readme-cover.png" in english_readme
    assert "setup --hermes-dir" in english_readme
    assert "Hermes Gateway Streaming And Thinking" in english_readme
    assert "streaming.enabled" in english_readme
    assert "display.platforms.feishu.streaming" in english_readme
    assert "Do not treat `display.show_reasoning`" in english_readme
    assert "thinking.delta" in english_readme
    assert "360 passed" in english_readme

    for name in expected_docs:
        zh_path = f"docs/{name}.md"
        en_path = f"docs/{name}.en.md"
        assert en_path in readme
        assert en_path in english_readme
        assert (ROOT / en_path).exists()
        assert f"[English]({name}.en.md)" in read_doc(zh_path)
        assert f"[中文]({name}.md)" in read_doc(en_path)


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


def test_docs_describe_event_forwarding_and_real_e2e_completion():
    readme = read_doc("README.md")
    architecture = read_doc("docs/architecture.md")
    todo = read_doc("TODO.md")
    docs = "\n".join(
        [
            readme,
            architecture,
            todo,
        ]
    )

    assert "真实 Feishu E2E 主链路" in readme
    assert "Hermes hook 到 sidecar `/events` 的 fail-open 转发链路已经落地" in architecture
    assert "Feishu CardKit HTTP client 已实现" in docs
    assert "真实 Hermes Gateway E2E" in docs
    assert "- [x] 补齐基于 Hermes fixture 和 mock sidecar 的最小 hook 事件转发验证。" in todo
    assert "- [x] 补齐官方 Hermes `v2026.4.23` Git tag 源码的安装/恢复 smoke test。" in todo
    assert "- [x] 在真实 Hermes Gateway 进程中做人工 smoke test。" in todo


def test_docs_describe_sidecar_process_management_scope():
    docs = "\n".join(
        [
            read_doc("README.md"),
            read_doc("docs/architecture.md"),
            read_doc("docs/testing.md"),
            read_doc("TODO.md"),
        ]
    )

    assert "start --config" in docs
    assert "status --config" in docs
    assert "stop --config" in docs
    assert "/health" in docs
    assert "PID/token" in docs
    assert "process_pid/process_token" in docs
    assert "POSIX" in docs
    assert "no-op client" in docs
    assert "- [x] 将 sidecar 进程管理从占位 `status` 扩展为可启动、可停止、可探活。" in docs


def test_docs_describe_sidecar_health_and_retry_metrics():
    docs = "\n".join(
        [
            read_doc("README.md"),
            read_doc("docs/architecture.md"),
            read_doc("docs/testing.md"),
            read_doc("TODO.md"),
        ]
    )

    assert "metrics" in docs
    assert "events_received" in docs
    assert "events_applied" in docs
    assert "events_rejected" in docs
    assert "feishu_update_retries" in docs
    assert "status" in docs
    assert "重复卡片" in docs
    assert "- [x] 增加 sidecar 健康检查和重试指标。" in docs


def test_docs_describe_feishu_http_client_and_live_smoke():
    docs = "\n".join(
        [
            read_doc("README.md"),
            read_doc("docs/architecture.md"),
            read_doc("docs/testing.md"),
            read_doc("TODO.md"),
        ]
    )

    assert "tenant token" in docs or "tenant access token" in docs
    assert "mock Feishu server" in docs
    assert "smoke-feishu-card" in docs
    assert "--chat-id" in docs
    assert "真实飞书应用做人工 CardKit smoke test" in docs
    assert "- [x] 实现 Feishu CardKit HTTP client，并用 mock server 验证 tenant token、发送和更新。" in docs
    assert "- [x] 提供 `smoke-feishu-card` 手动命令用于真实飞书卡片发送/更新验证。" in docs
    assert "- [x] 使用真实飞书应用做人工 CardKit smoke test，凭据仅使用本机配置或环境变量。" in docs
    assert "- [x] 完成真实飞书长卡片压力测试，同一张卡片更新到 16k 中文字符。" in docs


def test_docs_describe_hermes_detection_diagnostics():
    docs = "\n".join(
        [
            read_doc("README.md"),
            read_doc("docs/installer-safety.md"),
            read_doc("docs/testing.md"),
            read_doc("TODO.md"),
        ]
    )

    assert "doctor --config config.yaml.example --hermes-dir" in docs
    assert "version_source" in docs
    assert "minimum_supported_version" in docs
    assert "run_py_exists" in docs
    assert "reason" in docs
    assert "- [x] 增加安装前 Hermes 版本展示和更友好的错误提示。" in docs


def test_legacy_handoff_docs_do_not_claim_active_cardkit_completion():
    legacy_docs = "\n".join(
        [
            read_doc("legacy/docs/README_en.md"),
            read_doc("legacy/docs/QUICKSTART.md"),
            read_doc("legacy/docs/PROGRESS.md"),
        ]
    )

    assert "not the active runtime" in legacy_docs
    assert "Real Feishu CardKit create/update integration is still future work" in legacy_docs
    assert "Current mainline verification uses fixture Hermes + mock sidecar tests" in legacy_docs


def test_docs_describe_safe_legacy_to_sidecar_migration():
    docs = "\n".join(
        [
            read_doc("README.md"),
            read_doc("docs/installer-safety.md"),
            read_doc("docs/migration.md"),
            read_doc("TODO.md"),
        ]
    )

    assert "docs/migration.md" in docs
    assert "legacy/dual" in docs
    assert "sidecar-only" in docs
    assert "installer_v2.py" in docs
    assert "gateway_run_patch.py" in docs
    assert "patch_feishu.py" in docs
    assert "restore --hermes-dir" in docs
    assert "doctor --config" in docs
    assert "install --hermes-dir" in docs
    assert "fail-closed" in docs
    assert "不要把 App Secret" in docs
    assert "- [x] 编写从 legacy/dual 安装迁移到 sidecar-only 的安全迁移说明。" in docs


def test_docs_describe_e2e_visual_preview_materials():
    docs = "\n".join(
        [
            read_doc("README.md"),
            read_doc("docs/e2e-verification.md"),
            read_doc("docs/testing.md"),
            read_doc("TODO.md"),
        ]
    )
    svg = read_doc("docs/assets/e2e-card-preview.svg")
    preview_json = read_doc("docs/assets/e2e-card-preview.json")

    assert "docs/e2e-verification.md" in docs
    assert "e2e-card-preview.svg" in docs
    assert "e2e-card-preview.json" in docs
    assert "tools/generate_e2e_preview.py" in docs
    assert "思考中" in svg
    assert "已完成" in svg
    assert "工具调用 2 次" in svg
    assert "</think>" not in svg
    assert '"thinking"' in preview_json
    assert '"completed"' in preview_json
    assert "- [x] 增加端到端截图或录屏验证材料。" in docs


def test_docs_describe_release_readiness_boundaries():
    docs = "\n".join(
        [
            read_doc("README.md"),
            read_doc("docs/release-readiness.md"),
            read_doc("TODO.md"),
        ]
    )

    assert "docs/release-readiness.md" in docs
    assert "3.1.0" in docs
    assert "python3 -m pytest -q" in docs
    assert "真实 Hermes Gateway" in docs
    assert "真实飞书应用" in docs
    assert "App Secret" in docs
    assert "GitHub Actions" in docs
