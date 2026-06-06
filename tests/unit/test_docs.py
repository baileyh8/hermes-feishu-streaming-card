from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_doc(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_readme_documents_sidecar_only_and_supported_hermes_version():
    readme = read_doc("README.md")

    assert readme.startswith("# Hermes 飞书流式卡片插件\n")
    assert "V3.6.1" in readme
    assert "[English](README.en.md)" in readme
    assert "img.shields.io/github/stars/baileyh8/hermes-feishu-streaming-card" in readme
    assert "img.shields.io/github/v/release/baileyh8/hermes-feishu-streaming-card" in readme
    assert "img.shields.io/github/actions/workflow/status/baileyh8/hermes-feishu-streaming-card/tests.yml" in readme
    assert "img.shields.io/badge/Python-3.9%2B" in readme
    assert "img.shields.io/badge/Feishu%20%2F%20Lark-Streaming%20Cards" in readme
    assert "img.shields.io/badge/Runtime-Sidecar--only" in readme
    assert "docs/assets/readme-cover.png" in readme
    assert "项目亮点" in readme
    assert "解决的真实痛点" in readme
    assert "Hermes Agent Gateway 的飞书/Lark 回复变成一张持续更新的交互式卡片" in readme
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
    assert "V3.2" in readme
    assert "多 bot" in readme
    assert "群聊" in readme
    assert "bindings.chats" in readme
    assert "group_rules" in readme


def test_readme_documents_v340_hermes_compatibility():
    readme = read_doc("README.md")

    assert "V3.6.0" in readme
    assert "issue #41" in readme
    assert "PR #42" in readme
    assert "授权/选项按钮" in readme
    assert "issue #39" in readme
    assert "v0.14.0" in readme
    assert "0.15.x" in readme
    assert "v2026.5.16+" in readme
    assert "issue #31" in readme
    assert "issue #25" in readme
    assert "Hermes 0.13.0" in readme
    assert "旧版本" in readme
    assert "hook_strategy" in readme
    assert "gateway_run_013_plus" in readme
    assert "legacy_gateway_run" in readme
    assert "compatibility" in readme
    assert "anchor" in readme or "anchors" in readme
    assert "重新安装 hook" in readme
    assert "install --hermes-dir" in readme
    assert "issue #23" in readme
    assert "多 profile / multi bot" in readme
    assert "per-bot/profile title" in readme
    assert "cron final cards" in readme
    assert "attachment summaries + native media delivery" in readme
    assert "routing profile diagnostics" in readme
    assert "safe repair" in readme
    assert "reply card context" in readme


def test_english_readme_documents_v340_hermes_compatibility():
    readme = read_doc("README.en.md")

    assert "V3.6.1" in readme
    assert "issue #41" in readme
    assert "PR #42" in readme
    assert "Approval/choice buttons" in readme
    assert "issue #39" in readme
    assert "v0.14.0" in readme
    assert "0.15.x" in readme
    assert "v2026.5.16+" in readme
    assert "issue #31" in readme
    assert "issue #25" in readme
    assert "Hermes 0.13.0" in readme
    assert "older Hermes" in readme
    assert "hook_strategy" in readme
    assert "gateway_run_013_plus" in readme
    assert "legacy_gateway_run" in readme
    assert "compatibility" in readme
    assert "anchor" in readme or "anchors" in readme
    assert "Reinstall the hook" in readme
    assert "install --hermes-dir" in readme
    assert "issue #23" in readme
    assert "Multi-profile / multi-bot" in readme
    assert "per-bot/profile title" in readme
    assert "cron final cards" in readme
    assert "attachment summaries + native media delivery" in readme
    assert "routing profile diagnostics" in readme
    assert "safe `repair`" in readme
    assert "reply card context" in readme


def test_readme_documents_one_line_install_and_release_packages():
    readme = read_doc("README.md")
    english_readme = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    workflow = read_doc(".github/workflows/release-assets.yml")

    assert "curl -fsSL https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/main/install.sh | bash" in readme
    assert "irm https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/main/install.ps1 | iex" in readme
    assert "README-install.md" in readme
    assert "docs/release-notes-v3.6.1.md" in readme
    assert "docs/release-notes-v3.6.0.md" in readme or "v3.6.0" in readme
    assert "docs/release-notes-v3.5.2.md" in readme or "v3.5.2" in readme
    assert "docs/roadmap-v3.6.0.md" in readme
    assert "hermes-feishu-card-<version>-macos.tar.gz" in readme
    assert "hermes-feishu-card-<version>-linux.tar.gz" in readme
    assert "hermes-feishu-card-<version>-windows.zip" in readme

    assert "One-Line Install" in english_readme
    assert "README-install.md" in english_readme
    assert "bash install.sh" in install_doc
    assert "install.ps1" in install_doc
    assert "HFC_VERSION" in install_doc
    assert "v3.6.1" in install_doc

    assert (ROOT / "install.sh").exists()
    assert (ROOT / "install.ps1").exists()
    assert (ROOT / "README-install.md").exists()
    assert (ROOT / "docs/release-notes-v3.6.1.md").exists()
    assert (ROOT / "docs/release-notes-v3.6.0.md").exists()
    assert (ROOT / "docs/release-notes-v3.5.2.md").exists()
    assert (ROOT / "docs/roadmap-v3.6.0.md").exists()
    assert (ROOT / ".github/workflows/release-assets.yml").exists()
    assert "gh release upload" in workflow
    assert 'NAME="hermes-feishu-card-${TAG}"' in workflow
    assert "${NAME}-macos.tar.gz" in workflow
    assert "${NAME}-linux.tar.gz" in workflow
    assert "${NAME}-windows.zip" in workflow


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
    assert english_readme.startswith("# Hermes Feishu Streaming Card Plugin\n")
    assert "Hermes Feishu Streaming Card turns Hermes Agent Gateway replies" in english_readme
    assert "Project Highlights" in english_readme
    assert "Pain Points Solved" in english_readme
    assert "img.shields.io/github/stars/baileyh8/hermes-feishu-streaming-card" in english_readme
    assert "docs/assets/readme-cover.png" in english_readme
    assert "setup --hermes-dir" in english_readme
    assert "Hermes Gateway Streaming And Thinking" in english_readme
    assert "streaming.enabled" in english_readme
    assert "display.platforms.feishu.streaming" in english_readme
    assert "Do not treat `display.show_reasoning`" in english_readme
    assert "thinking.delta" in english_readme
    assert "Multi-bot" in english_readme
    assert "group chat" in english_readme
    assert "pytest" in english_readme
    assert "425 passed" not in readme
    assert "398 passed" not in readme
    assert "425 passed" not in english_readme
    assert "398 passed" not in english_readme

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
    assert "等待选择" in event_protocol
    assert "已完成" in event_protocol
    assert "interaction.requested" in event_protocol


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
    assert "hook_strategy" in docs
    assert "compatibility" in docs
    assert "anchor" in docs or "anchors" in docs
    assert "reason" in docs
    assert "--explain" in docs
    assert "--json" in docs
    assert "install_state" in docs
    assert "recommendations" in docs
    assert "- [x] 增加安装前 Hermes 版本展示和更友好的错误提示。" in docs


def test_migration_docs_describe_v340_upgrade_commands_and_profile_id():
    zh = read_doc("docs/migration.md")
    en = read_doc("docs/migration.en.md")

    for doc in (zh, en):
        assert "V3.4.0" in doc
        assert "python3 -m hermes_feishu_card.cli stop --config ~/.hermes_feishu_card/config.yaml" in doc
        assert 'pip install -e ".[test]" --upgrade' in doc
        assert (
            "python3 -m hermes_feishu_card.cli doctor --config ~/.hermes_feishu_card/config.yaml "
            "--hermes-dir ~/.hermes/hermes-agent"
        ) in doc
        assert "python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes" in doc
        assert "python3 -m hermes_feishu_card.cli start --config ~/.hermes_feishu_card/config.yaml" in doc
        assert "HERMES_FEISHU_CARD_PROFILE_ID" in doc


def test_changelog_documents_v340_release_notes():
    changelog = read_doc("CHANGELOG.md")

    assert "## V3.4.0 — 2026-05-10" in changelog
    assert "Hermes 0.13+" in changelog
    assert "gateway_run_013_plus" in changelog
    assert "legacy_gateway_run" in changelog
    assert "Per-bot/profile titles" in changelog
    assert "Cron final card delivery" in changelog
    assert "Attachment summaries with native media delivery" in changelog
    assert "Card reply context" in changelog


def test_changelog_documents_v341_release_notes():
    changelog = read_doc("CHANGELOG.md")

    assert "## V3.4.1 — 2026-05-14" in changelog
    assert "issue #25" in changelog
    assert "event_message_id" in changelog
    assert "_preview_fallback_message_id" in changelog
    assert "_create_active_fallback_message_id" in changelog


def test_changelog_documents_v342_release_notes():
    changelog = read_doc("CHANGELOG.md")

    assert "## V3.4.2 — 2026-05-21" in changelog
    assert "issue #31" in changelog
    assert "PATCH updates" in changelog
    assert "sequence numbers" in changelog
    assert "issue #23" in changelog


def test_changelog_documents_v343_release_notes():
    changelog = read_doc("CHANGELOG.md")

    assert "## V3.4.3 — 2026-05-27" in changelog
    assert "issue #39" in changelog
    assert "DeepSeek V4 Pro" in changelog
    assert "Markdown" in changelog
    assert "v0.14.0" in changelog
    assert "v2026.5.16+" in changelog


def test_changelog_documents_v352_release_notes():
    changelog = read_doc("CHANGELOG.md")
    release_notes = read_doc("docs/release-notes-v3.5.2.md")

    assert "## V3.5.2 — 2026-06-04" in changelog
    assert "## V3.5.1 — 2026-06-01" in changelog
    assert "## V3.5.0 — 2026-06-01" in changelog
    assert "Cross-platform installers" in changelog
    assert "externally-managed-environment" in changelog
    assert "install.ps1" in changelog
    assert "V3.5.2 Release Notes" in release_notes
    assert "hermes-feishu-card-v3.5.2-macos.tar.gz" in release_notes
    assert "issue #41" in changelog
    assert "PR #42" in changelog
    assert "interaction.requested" in changelog
    assert "MAIN_CONTENT_CHUNK_CHARS" in changelog
    assert "append_block" in changelog


def test_changelog_documents_v360_release_notes():
    changelog = read_doc("CHANGELOG.md")
    release_notes = read_doc("docs/release-notes-v3.6.0.md")

    assert "## V3.6.0 — 2026-06-04" in changelog
    assert "doctor --json" in changelog
    assert "doctor --explain" in changelog
    assert "repair --hermes-dir" in changelog
    assert "setup --repair" in changelog
    assert "media_files" in changelog
    assert "smoke-feishu-card --profile-id" in changelog
    assert "bots test --profile-id" in changelog
    assert "/health.routing.profiles" in changelog
    assert "v2026.5.29" in changelog
    assert "V3.6.0 Release Notes" in release_notes
    assert "hermes-feishu-card-v3.6.0-macos.tar.gz" in release_notes
    assert "Docker packaging is intentionally out of scope" in release_notes


def test_changelog_documents_v361_release_notes():
    changelog = read_doc("CHANGELOG.md")
    release_notes = read_doc("docs/release-notes-v3.6.1.md")

    assert "## V3.6.1 — 2026-06-06" in changelog
    assert "issue #47" in changelog
    assert "0.15.1" in changelog
    assert "v0.15.1" in changelog
    assert "gateway_run_013_plus" in changelog
    assert "doctor --explain" in changelog
    assert "V3.6.1 Release Notes" in release_notes
    assert "hermes-feishu-card-v3.6.1-macos.tar.gz" in release_notes
    assert "0.15.x" in release_notes


def test_config_example_documents_profile_and_bot_card_titles():
    config = read_doc("config.yaml.example")

    assert "profiles.<id>.card.title" in config
    assert "bots.items.<id>.card.title" in config
    assert "bot title wins over profile title" in config
    assert "title: Sales Bot" in config
    assert "title: Default Profile" in config
    assert "title: Work Bot" in config
    assert "title: Work Profile" in config


def test_testing_docs_describe_v340_doctor_output_without_stale_counts():
    zh = read_doc("docs/testing.md")
    en = read_doc("docs/testing.en.md")
    e2e_zh = read_doc("docs/e2e-verification.md")
    e2e_en = read_doc("docs/e2e-verification.en.md")

    for doc in (zh, en):
        assert "hook_strategy" in doc
        assert "compatibility" in doc
        assert "anchor" in doc or "anchors" in doc
        assert "gateway_run_013_plus" in doc
        assert "legacy_gateway_run" in doc

    for doc in (zh, en, e2e_zh, e2e_en):
        assert "425 passed" not in doc
        assert "398 passed" not in doc


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
    assert "- [x] 编写从 legacy/dual" in docs and "安装迁移到 sidecar-only 的安全迁移说明" in docs


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
    assert "端到端截图" in docs and "e2e-card-preview" in docs


def test_docs_describe_release_readiness_boundaries():
    release_readiness = read_doc("docs/release-readiness.md")
    docs = "\n".join(
        [
            read_doc("README.md"),
            release_readiness,
            read_doc("TODO.md"),
        ]
    )

    assert "docs/release-readiness.md" in docs
    assert "3.6.1" in release_readiness
    assert "Release assets workflow" in release_readiness
    assert "install.ps1" in release_readiness
    assert "3.1.0" not in release_readiness
    assert "interaction.requested" in release_readiness
    assert "append_block" in release_readiness
    assert "MAIN_CONTENT_CHUNK_CHARS" in release_readiness
    assert "doctor --json" in release_readiness
    assert "repair --hermes-dir" in release_readiness
    assert "/health.routing.profiles" in release_readiness
    assert "0.15.x" in release_readiness
    assert "python3 -m pytest -q" in docs
    assert "真实 Hermes Gateway" in docs
    assert "真实飞书应用" in docs
    assert "App Secret" in docs
    assert "GitHub Actions" in docs
