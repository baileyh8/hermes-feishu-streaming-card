from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def read_doc(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_maintainer_docs_define_reliable_notice_delivery_contract():
    event_flow = read_doc("docs/wiki/event-flow.md")
    maintenance = read_doc("docs/wiki/maintenance-guide.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")
    combined = "\n".join((event_flow, maintenance, acceptance))

    for marker in (
        "delivery_uuid",
        "not_sent",
        "unknown",
        "feishu_send_retries",
        "feishu_send_unknown_outcomes",
        "feishu_noop_attempts",
        "notice_native_fallbacks",
        "notice_uncertain_warnings",
    ):
        assert marker in combined
    assert "不重试 `/events`" in combined
    assert "原始通知文本" in combined
    assert "不重复原始通知文本" in combined
    assert "⚠️ 一条运行提示的卡片投递结果无法确认，请稍后查看 /hfc status。" in combined


def test_readme_documents_sidecar_only_and_supported_hermes_version():
    readme = read_doc("README.md")
    guide = read_doc("docs/user-guide.md")

    assert readme.startswith("# Hermes 飞书流式卡片插件\n")
    assert "[English](README.en.md)" in readme
    assert "img.shields.io/github/stars/baileyh8/hermes-feishu-streaming-card" in readme
    assert "img.shields.io/github/v/release/baileyh8/hermes-feishu-streaming-card" in readme
    assert "img.shields.io/github/actions/workflow/status/baileyh8/hermes-feishu-streaming-card/tests.yml" in readme
    assert "img.shields.io/badge/Python-3.9%2B" in readme
    assert "img.shields.io/badge/Feishu%20%2F%20Lark-Streaming%20Cards" in readme
    assert "img.shields.io/badge/Runtime-Sidecar--only" in readme
    assert "docs/assets/readme-cover.png" in readme
    assert "docs/assets/feishu-card-showcase-v385.png" in readme
    assert "docs/assets/feishu-topic-card-showcase-v389.png" in readme
    assert "docs/user-guide.md" in readme
    assert "PR #76" in readme
    assert "PR #87" in readme
    assert "PR #88" in readme
    assert "PR #91" in readme
    assert "PR #77" in readme
    assert "colinaaa" in readme
    assert "zayn-0101" in readme
    assert "你能看到什么" in readme
    assert "适用场景" in readme
    assert "Hermes Agent Gateway 的飞书/Lark 回复变成一张持续更新的交互式卡片" in readme
    assert "/hfc status" in readme
    assert "HERMES_FEISHU_CARD_DELTA_COALESCE_MS" in readme
    assert "sidecar-only" in readme.lower()
    assert "setup --hermes-dir" in readme
    assert "整合安装器" in readme
    assert "streaming.enabled" in readme
    assert "display.platforms.feishu.streaming" in readme
    assert "不要把 `display.show_reasoning`" in readme
    assert "thinking.delta" in readme
    assert "v2026.4.23" in readme
    assert "Git tag `v2026.4.23+`" in readme
    assert len(readme.splitlines()) <= 260
    assert (ROOT / "docs/assets/readme-cover.png").exists()
    assert (ROOT / "docs/assets/feishu-card-showcase-v385.png").exists()
    assert (ROOT / "docs/assets/feishu-weather-card.png").exists()
    assert "V3.6.6" in guide
    assert "V3.6.5" in guide
    assert "V3.6.4" in guide
    assert "V3.6.3" in guide
    assert "V3.6.2" in guide
    assert "V3.2" in guide
    assert "多 bot" in readme
    assert "群聊" in readme
    assert "bindings.chats" in readme
    assert "group_rules" in guide


def test_readme_documents_v340_hermes_compatibility():
    readme = read_doc("README.md")
    guide = read_doc("docs/user-guide.md")
    docs = readme + "\n" + guide

    assert "V3.6.0" in docs
    assert "issue #41" in docs
    assert "PR #42" in docs
    assert "授权/选项按钮" in docs
    assert "issue #39" in docs
    assert "v0.14.0" in docs
    assert "0.15.x" in docs
    assert "v2026.5.16+" in docs
    assert "issue #31" in docs
    assert "issue #25" in docs
    assert "Hermes 0.13.0" in docs
    assert "旧版本" in docs
    assert "hook_strategy" in docs
    assert "gateway_run_013_plus" in docs
    assert "legacy_gateway_run" in docs
    assert "compatibility" in docs
    assert "anchor" in docs or "anchors" in docs
    assert "重新安装 hook" in docs
    assert "install --hermes-dir" in docs
    assert "issue #23" in docs
    assert "多 profile / multi bot" in docs
    assert "per-bot/profile title" in docs
    assert "cron final cards" in docs
    assert "attachment summaries + native media delivery" in docs
    assert "routing profile diagnostics" in docs
    assert "safe repair" in docs
    assert "reply card context" in docs


def test_english_readme_documents_v340_hermes_compatibility():
    readme = read_doc("README.en.md")
    guide = read_doc("docs/user-guide.en.md")
    docs = readme + "\n" + guide

    assert "V3.6.6" in guide
    assert "V3.6.5" in guide
    assert "V3.6.4" in guide
    assert "V3.6.3" in guide
    assert "V3.6.2" in guide
    assert "issue #41" in docs
    assert "PR #42" in docs
    assert "Approval/choice interactions" in docs
    assert "issue #39" in docs
    assert "v0.14.0" in docs
    assert "0.15.x" in docs
    assert "v2026.5.16+" in docs
    assert "issue #31" in docs
    assert "issue #25" in docs
    assert "Hermes 0.13.0" in docs
    assert "older Hermes" in docs
    assert "hook_strategy" in docs
    assert "gateway_run_013_plus" in docs
    assert "legacy_gateway_run" in docs
    assert "compatibility" in docs
    assert "anchor" in docs or "anchors" in docs
    assert "Reinstall the hook" in docs
    assert "install --hermes-dir" in docs
    assert "issue #23" in docs
    assert "Multi-profile / multi-bot" in docs
    assert "per-bot/profile title" in docs
    assert "cron final cards" in docs
    assert "attachment summaries + native media delivery" in docs
    assert "routing profile diagnostics" in docs
    assert "safe `repair`" in docs
    assert "reply card context" in docs


def test_readme_documents_one_line_install_and_release_packages():
    readme = read_doc("README.md")
    english_readme = read_doc("README.en.md")
    guide = read_doc("docs/user-guide.md")
    install_doc = read_doc("README-install.md")
    workflow = read_doc(".github/workflows/release-assets.yml")

    assert "curl -fsSL https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/main/install.sh | bash" in readme
    assert "irm https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/main/install.ps1 | iex" in readme
    assert "README-install.md" in readme
    assert "install-docker.sh" in readme
    assert "docker-compose.example.yml" in readme
    assert "Docker" in install_doc
    assert "v3.8.5" not in install_doc
    assert "version_source: gateway anchors" in install_doc
    assert "docs/release-notes-v3.8.17.md" in readme
    assert "docs/release-notes-v3.8.18.md" in readme
    assert "docs/release-notes-v3.8.16.md" in readme
    assert "docs/release-notes-v3.8.15.md" in readme
    assert "docs/release-notes-v3.8.14.md" in readme
    assert "docs/release-notes-v3.8.13.md" in readme
    assert "docs/release-notes-v3.8.12.md" in readme
    assert "docs/release-notes-v3.8.11.md" in readme
    assert "docs/release-notes-v3.8.10.md" in readme
    assert "docs/release-notes-v3.8.9.md" in readme
    assert "docs/release-notes-v3.8.8.md" in readme
    assert "docs/release-notes-v3.8.7.md" in readme
    assert "docs/release-notes-v3.8.6.md" in readme
    assert "docs/release-notes-v3.8.5.md" in readme
    assert "release-notes-v3.8.4.md" in guide
    assert "release-notes-v3.8.3.md" in guide
    assert "release-notes-v3.8.2.md" in guide
    assert "release-notes-v3.8.1.md" in guide
    assert "release-notes-v3.8.0.md" in guide
    assert "release-notes-v3.6.6.md" in guide
    assert "release-notes-v3.6.5.md" in guide
    assert "release-notes-v3.6.4.md" in guide
    assert "release-notes-v3.6.3.md" in guide
    assert "release-notes-v3.6.2.md" in guide
    assert "release-notes-v3.6.1.md" in guide
    assert "release-notes-v3.6.0.md" in guide or "v3.6.0" in guide
    assert "release-notes-v3.5.2.md" in guide or "v3.5.2" in guide
    assert "roadmap-v3.6.0.md" in guide
    assert "hermes-feishu-card-<version>-macos.tar.gz" in guide
    assert "hermes-feishu-card-<version>-linux.tar.gz" in guide
    assert "hermes-feishu-card-<version>-windows.zip" in guide

    assert "Quick Install" in english_readme
    assert "README-install.md" in english_readme
    assert "bash install.sh" in install_doc
    assert "install.ps1" in install_doc
    assert "HFC_VERSION" in install_doc
    assert "v3.6.6" in install_doc

    assert (ROOT / "install.sh").exists()
    assert (ROOT / "install.ps1").exists()
    assert (ROOT / "README-install.md").exists()
    assert (ROOT / "docs/release-notes-v3.6.6.md").exists()
    assert (ROOT / "docs/release-notes-v3.6.5.md").exists()
    assert (ROOT / "docs/release-notes-v3.6.4.md").exists()
    assert (ROOT / "docs/release-notes-v3.6.3.md").exists()
    assert (ROOT / "docs/release-notes-v3.6.2.md").exists()
    assert (ROOT / "docs/release-notes-v3.6.1.md").exists()
    assert (ROOT / "docs/release-notes-v3.6.0.md").exists()
    assert (ROOT / "docs/release-notes-v3.5.2.md").exists()
    assert (ROOT / "docs/roadmap-v3.6.0.md").exists()
    assert (ROOT / "install-docker.sh").exists()
    assert (ROOT / "docker-compose.example.yml").exists()
    assert (ROOT / "docs/release-notes-v3.8.17.md").exists()
    assert (ROOT / "docs/release-notes-v3.8.16.md").exists()
    assert (ROOT / "docs/release-notes-v3.8.15.md").exists()
    assert (ROOT / "docs/release-notes-v3.8.14.md").exists()
    assert (ROOT / "docs/release-notes-v3.8.13.md").exists()
    assert (ROOT / "docs/release-notes-v3.8.12.md").exists()
    assert (ROOT / "docs/release-notes-v3.8.11.md").exists()
    assert (ROOT / "docs/release-notes-v3.8.10.md").exists()
    assert (ROOT / "docs/release-notes-v3.8.9.md").exists()
    assert (ROOT / "docs/release-notes-v3.8.8.md").exists()
    assert (ROOT / "docs/release-notes-v3.8.7.md").exists()
    assert (ROOT / "docs/release-notes-v3.8.6.md").exists()
    assert (ROOT / "docs/release-notes-v3.8.5.md").exists()
    assert (ROOT / "docs/release-notes-v3.8.4.md").exists()
    assert (ROOT / "docs/release-notes-v3.8.3.md").exists()
    assert (ROOT / "docs/release-notes-v3.8.2.md").exists()
    assert (ROOT / "docs/release-notes-v3.8.1.md").exists()
    assert (ROOT / "docs/release-notes-v3.8.0.md").exists()
    assert (ROOT / "docs/release-notes-v3.7.0.md").exists()
    assert (ROOT / ".github/workflows/release-assets.yml").exists()
    assert "gh release upload" in workflow
    assert 'NAME="hermes-feishu-card-${TAG}"' in workflow
    assert "${NAME}-macos.tar.gz" in workflow
    assert "${NAME}-linux.tar.gz" in workflow
    assert "${NAME}-windows.zip" in workflow


def test_v3817_release_notes_are_linked():
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    v3818_release_notes = Path("docs/release-notes-v3.8.18.md")
    v3817_release_notes = Path("docs/release-notes-v3.8.17.md")
    v3816_release_notes = Path("docs/release-notes-v3.8.16.md")
    v3815_release_notes = Path("docs/release-notes-v3.8.15.md")
    v3814_release_notes = Path("docs/release-notes-v3.8.14.md")
    v3813_release_notes = Path("docs/release-notes-v3.8.13.md")
    v3812_release_notes = Path("docs/release-notes-v3.8.12.md")
    v3811_release_notes = Path("docs/release-notes-v3.8.11.md")
    v3810_release_notes = Path("docs/release-notes-v3.8.10.md")
    v389_release_notes = Path("docs/release-notes-v3.8.9.md")
    v388_release_notes = Path("docs/release-notes-v3.8.8.md")
    v387_release_notes = Path("docs/release-notes-v3.8.7.md")
    v386_release_notes = Path("docs/release-notes-v3.8.6.md")
    v385_release_notes = Path("docs/release-notes-v3.8.5.md")
    v384_release_notes = Path("docs/release-notes-v3.8.4.md")
    v383_release_notes = Path("docs/release-notes-v3.8.3.md")
    release_notes = Path("docs/release-notes-v3.8.2.md")
    compose = Path("docker-compose.example.yml").read_text(encoding="utf-8")

    assert v3818_release_notes.exists()
    assert "## V3.8.18 — 2026-07-10" in changelog
    assert "V3.8.18" in changelog
    assert "[docs/release-notes-v3.8.18.md](docs/release-notes-v3.8.18.md)" in changelog
    v3818_text = v3818_release_notes.read_text(encoding="utf-8")
    assert "PR #91" in v3818_text
    assert "@colinaaa" in v3818_text
    assert "thread_id" in v3818_text
    assert "issue #90" in v3818_text
    assert "hermes-feishu-card-v3.8.18-macos.tar.gz" in v3818_text
    assert v3817_release_notes.exists()
    assert "## V3.8.17 — 2026-07-09" in changelog
    assert "V3.8.17" in changelog
    assert "[docs/release-notes-v3.8.17.md](docs/release-notes-v3.8.17.md)" in changelog
    v3817_text = v3817_release_notes.read_text(encoding="utf-8")
    assert "PR #77" in v3817_text
    assert "@zayn-0101" in v3817_text
    assert "deliver" in v3817_text
    assert "origin" in v3817_text
    assert "all" in v3817_text
    assert "local" in v3817_text
    assert "hermes-feishu-card-v3.8.17-macos.tar.gz" in v3817_text
    assert v3816_release_notes.exists()
    assert "## V3.8.16 — 2026-07-09" in changelog
    assert "V3.8.16" in changelog
    assert "[docs/release-notes-v3.8.16.md](docs/release-notes-v3.8.16.md)" in changelog
    v3816_text = v3816_release_notes.read_text(encoding="utf-8")
    assert "issue #89" in v3816_text
    assert "PR #88" in v3816_text
    assert "@colinaaa" in v3816_text
    assert "message_id" in v3816_text
    assert "topic groups" in v3816_text
    assert "hermes-feishu-card-v3.8.16-macos.tar.gz" in v3816_text
    assert v3815_release_notes.exists()
    assert "## V3.8.15 — 2026-07-09" in changelog
    assert "V3.8.15" in changelog
    assert "[docs/release-notes-v3.8.15.md](docs/release-notes-v3.8.15.md)" in changelog
    v3815_text = v3815_release_notes.read_text(encoding="utf-8")
    assert "issue #82" in v3815_text
    assert "input file" in v3815_text
    assert "MEDIA:/tmp/..." in v3815_text
    assert "duplicate native Feishu/Lark reply" in v3815_text
    assert "hermes-feishu-card-v3.8.15-macos.tar.gz" in v3815_text
    assert v3814_release_notes.exists()
    assert "## V3.8.14 — 2026-07-09" in changelog
    assert "V3.8.14" in changelog
    assert "[docs/release-notes-v3.8.14.md](docs/release-notes-v3.8.14.md)" in changelog
    v3814_text = v3814_release_notes.read_text(encoding="utf-8")
    assert "issue #86" in v3814_text
    assert "PR #87" in v3814_text
    assert "interaction.select" in v3814_text
    assert "/card/actions" in v3814_text
    assert "hermes-feishu-card-v3.8.14-macos.tar.gz" in v3814_text
    assert v3813_release_notes.exists()
    assert "## V3.8.13 — 2026-07-08" in changelog
    assert "V3.8.13" in changelog
    assert "[docs/release-notes-v3.8.13.md](docs/release-notes-v3.8.13.md)" in changelog
    v3813_text = v3813_release_notes.read_text(encoding="utf-8")
    assert "v2026.7.7.2" in v3813_text
    assert "VERSION + gateway anchors" in v3813_text
    assert "stale install state" in v3813_text
    assert "hermes-feishu-card-v3.8.13-macos.tar.gz" in v3813_text
    assert v3812_release_notes.exists()
    assert "## V3.8.12 — 2026-07-08" in changelog
    assert "V3.8.12" in changelog
    assert "[docs/release-notes-v3.8.12.md](docs/release-notes-v3.8.12.md)" in changelog
    v3812_text = v3812_release_notes.read_text(encoding="utf-8")
    assert "issue #82" in v3812_text
    assert "native_delivery" in v3812_text
    assert "attachment summaries" in v3812_text
    assert "hermes-feishu-card-v3.8.12-macos.tar.gz" in v3812_text
    assert v3811_release_notes.exists()
    assert "## V3.8.11 — 2026-07-08" in changelog
    assert "V3.8.11" in changelog
    assert "[docs/release-notes-v3.8.11.md](docs/release-notes-v3.8.11.md)" in changelog
    v3811_text = v3811_release_notes.read_text(encoding="utf-8")
    assert "Unknown command /hfc" in v3811_text
    assert "handled: true" in v3811_text
    assert "hermes-feishu-card-v3.8.11-macos.tar.gz" in v3811_text
    assert v3810_release_notes.exists()
    assert "## V3.8.10 — 2026-07-07" in changelog
    assert "V3.8.10" in changelog
    assert "[docs/release-notes-v3.8.10.md](docs/release-notes-v3.8.10.md)" in changelog
    v3810_text = v3810_release_notes.read_text(encoding="utf-8")
    assert "group" in v3810_text
    assert "bindings.group_rules" in v3810_text
    assert "tool.updated" in v3810_text
    assert "hermes-feishu-card-v3.8.10-macos.tar.gz" in v3810_text
    assert v389_release_notes.exists()
    assert "## V3.8.9 — 2026-07-04" in changelog
    assert "V3.8.9" in changelog
    assert "[docs/release-notes-v3.8.9.md](docs/release-notes-v3.8.9.md)" in changelog
    v389_text = v389_release_notes.read_text(encoding="utf-8")
    assert "reply_to_message_id" in v389_text
    assert "system.notice" in v389_text
    assert "source.message_id" in v389_text
    assert "hermes-feishu-card-v3.8.9-macos.tar.gz" in v389_text
    assert v388_release_notes.exists()
    assert "## V3.8.8 — 2026-07-03" in changelog
    assert "V3.8.8" in changelog
    assert "[docs/release-notes-v3.8.8.md](docs/release-notes-v3.8.8.md)" in changelog
    v388_text = v388_release_notes.read_text(encoding="utf-8")
    assert "system.notice" in v388_text
    assert "Working" in v388_text
    assert "self-improvement" in v388_text
    assert "hermes-feishu-card-v3.8.8-macos.tar.gz" in v388_text
    assert v387_release_notes.exists()
    assert "## V3.8.7 — 2026-07-02" in changelog
    assert "V3.8.7" in changelog
    assert "[docs/release-notes-v3.8.7.md](docs/release-notes-v3.8.7.md)" in changelog
    v387_text = v387_release_notes.read_text(encoding="utf-8")
    assert "issue #75" in v387_text
    assert "message.started" in v387_text
    assert "answer.delta" in v387_text
    assert "hermes-feishu-card-v3.8.7-macos.tar.gz" in v387_text
    assert v386_release_notes.exists()
    assert "## V3.8.6 — 2026-07-02" in changelog
    assert "V3.8.6" in changelog
    assert "[docs/release-notes-v3.8.6.md](docs/release-notes-v3.8.6.md)" in changelog
    v386_text = v386_release_notes.read_text(encoding="utf-8")
    assert "issue #70" in v386_text
    assert "Hermes v0.18.0" in v386_text
    assert "v2026.7.1" in v386_text
    assert "version_source: gateway anchors" in v386_text
    assert "hermes-feishu-card-v3.8.6-macos.tar.gz" in v386_text
    assert v385_release_notes.exists()
    assert "## V3.8.5 — 2026-07-02" in changelog
    assert "V3.8.5" in changelog
    assert "[docs/release-notes-v3.8.5.md](docs/release-notes-v3.8.5.md)" in changelog
    v385_text = v385_release_notes.read_text(encoding="utf-8")
    assert "始终允许" in v385_text
    assert "event=event" in v385_text
    assert "hermes-feishu-card-v3.8.5-macos.tar.gz" in v385_text
    assert v384_release_notes.exists()
    assert "## V3.8.4 — 2026-07-01" in changelog
    assert "V3.8.4" in changelog
    assert "[docs/release-notes-v3.8.4.md](docs/release-notes-v3.8.4.md)" in changelog
    v384_text = v384_release_notes.read_text(encoding="utf-8")
    assert "Feishu WebSocket 原生命令卡片" in v384_text
    assert "tools.slash_confirm.resolve" in v384_text
    assert "hermes-feishu-card-v3.8.4-macos.tar.gz" in v384_text
    assert v383_release_notes.exists()
    assert "## V3.8.3 — 2026-07-01" in changelog
    assert "V3.8.3" in changelog
    assert "[docs/release-notes-v3.8.3.md](docs/release-notes-v3.8.3.md)" in changelog
    v383_text = v383_release_notes.read_text(encoding="utf-8")
    assert "独立 slash 确认卡片" in v383_text
    assert "`/update` 不弹交互卡片" in v383_text
    assert "hermes-feishu-card-v3.8.3-macos.tar.gz" in v383_text
    assert release_notes.exists()
    assert "## V3.8.2 — 2026-07-01" in changelog
    assert "V3.8.2" in changelog
    assert "[docs/release-notes-v3.8.2.md](docs/release-notes-v3.8.2.md)" in changelog
    assert "## V3.8.1 — 2026-07-01" in changelog
    assert "V3.8.1" in changelog
    assert "[docs/release-notes-v3.8.1.md](docs/release-notes-v3.8.1.md)" in changelog
    assert "## V3.8.0 — 2026-07-01" in changelog
    assert "V3.8.0" in changelog
    assert "[docs/release-notes-v3.8.0.md](docs/release-notes-v3.8.0.md)" in changelog
    release_text = release_notes.read_text(encoding="utf-8")
    assert "pre-tool answer" in release_text
    assert "thinking.delta" in release_text
    assert "feishu-v382-readme-showcase.png" in release_text
    assert "hermes-feishu-card-v3.8.2-macos.tar.gz" in release_text


def test_todo_points_to_v38_public_plan_docs():
    todo = read_doc("TODO.md")

    assert "## V3.8 / V3.9 / V3.10 / V4.0 系列路线" in todo
    for version in ("V3.8.0", "V3.8.18", "V3.9.0", "V3.9.1", "V3.10.0", "V4.0.0", "V4.0.1", "V4.0.2", "V4.0.3", "V4.0.4", "V4.0.5", "V4.0.6", "V4.0.7"):
        assert version in todo
    assert "### V3.8.2：卡片 timeline 阅读体验补丁（已完成）" in todo
    assert "### V3.8.3：独立命令卡片（已完成）" in todo
    assert "### V3.8.4：Feishu WebSocket 命令卡片热修（已完成）" in todo
    assert "### V3.8.5：命令结果反馈卡片补丁（已完成）" in todo
    assert "### V3.8.6：Docker / Hermes v0.18.0 兼容补丁（已完成）" in todo
    assert "### V3.8.7：缺失 message.started 的新版 Hermes 流修复（已完成）" in todo
    assert "### V3.8.8：Hermes 原生系统提示卡片化（已完成）" in todo
    assert "### V3.8.9：飞书话题卡片连续更新补丁（已完成）" in todo
    assert "### V3.8.10：群聊能力与工具详情增强（已完成）" in todo
    assert "### V3.8.11：`/hfc` 原生未知命令抑制补丁（已完成）" in todo
    assert "### V3.8.12：附件摘要重复 reply 抑制补丁（已完成）" in todo
    assert "### V3.8.13：Hermes 升级兼容补丁（已完成）" in todo
    assert "### V3.8.14：WebSocket interaction.select 交互卡片补丁（已完成）" in todo
    assert "### V3.8.15：输入附件重复 reply 抑制补丁（已完成）" in todo
    assert "### V3.8.16：话题群 message_id 复用新卡补丁（已完成）" in todo
    assert "PR #88" in todo
    assert "@colinaaa" in todo
    assert "### V3.8.17：cron 路由意图卡片投递补丁（已完成）" in todo
    assert "PR #77" in todo
    assert "@zayn-0101" in todo
    assert "### V3.8.18：cron 话题线程回传补丁（已完成）" in todo
    assert "PR #91" in todo
    assert "### V3.8.x 后续维护与扩展面（待办）" in todo
    assert "[docs/superpowers/specs/2026-06-30-v3-8-design.md](docs/superpowers/specs/2026-06-30-v3-8-design.md)" in todo
    assert "[docs/superpowers/plans/2026-06-30-v3-8-card-ux-stability.md](docs/superpowers/plans/2026-06-30-v3-8-card-ux-stability.md)" in todo
    assert "docs/roadmap-v3.6.0.md" not in todo


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
    assert "/hfc status" in english_readme
    assert "HERMES_FEISHU_CARD_DELTA_COALESCE_MS" in english_readme
    assert "What You Get" in english_readme
    assert "Problems Solved" in english_readme
    assert "img.shields.io/github/stars/baileyh8/hermes-feishu-streaming-card" in english_readme
    assert "docs/assets/readme-cover.png" in english_readme
    assert "docs/assets/feishu-card-showcase-v385.png" in english_readme
    assert "PR #76" in english_readme
    assert "PR #87" in english_readme
    assert "PR #88" in english_readme
    assert "PR #91" in english_readme
    assert "PR #77" in english_readme
    assert "colinaaa" in english_readme
    assert "zayn-0101" in english_readme
    assert "setup --hermes-dir" in english_readme
    assert "Hermes Streaming Config" in english_readme
    assert "streaming.enabled" in english_readme
    assert "display.platforms.feishu.streaming" in english_readme
    assert "Do not treat `display.show_reasoning`" in english_readme
    assert "thinking.delta" in english_readme
    assert "Multi-bot" in english_readme
    assert "group chat" in english_readme
    assert "pytest" in read_doc("docs/testing.en.md")
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
    assert "thread_id" in event_protocol
    assert "reply API" in event_protocol


def test_docs_describe_event_forwarding_and_real_e2e_completion():
    readme = read_doc("README.md")
    guide = read_doc("docs/user-guide.md")
    architecture = read_doc("docs/architecture.md")
    todo = read_doc("TODO.md")
    docs = "\n".join(
        [
            readme,
            guide,
            architecture,
            todo,
        ]
    )

    assert "真实 Feishu E2E 主链路" in docs
    assert "Hermes hook 到 sidecar `/events` 的 fail-open 转发链路已经落地" in architecture
    assert "Feishu CardKit HTTP client 已实现" in docs
    assert "真实 Hermes Gateway E2E" in docs
    assert "- [x] 补齐基于 Hermes fixture 和 mock sidecar 的最小 hook 事件转发验证。" in todo
    assert "- [x] 补齐官方 Hermes `v2026.4.23` Git tag 源码的安装/恢复 smoke test。" in todo
    assert "- [x] 在真实 Hermes Gateway 进程中做人工 smoke test。" in todo


def test_docs_describe_secure_event_transport_and_current_feishu_state():
    readme = read_doc("README.md")
    english_readme = read_doc("README.en.md")
    guide = read_doc("docs/user-guide.md")
    english_guide = read_doc("docs/user-guide.en.md")
    architecture = read_doc("docs/architecture.md")
    english_architecture = read_doc("docs/architecture.en.md")
    config = read_doc("config.yaml.example")

    assert "真实飞书应用联调仍未完成" not in architecture
    assert "真实飞书应用联调仍是后续阶段" not in architecture
    for doc in (readme, guide, architecture):
        assert "本机进程互信" in doc
        assert "allow_non_loopback" in doc
        assert "事件鉴权" in doc
    for doc in (english_readme, english_guide, english_architecture):
        assert "local-process trust" in doc
        assert "allow_non_loopback" in doc
        assert "event authentication" in doc
    assert "allow_non_loopback: false" in config
    assert "不要把 sidecar 未鉴权暴露" in config
    assert "Do not expose an unauthenticated sidecar" in config


def test_install_and_maintainer_docs_define_event_security_and_fail_open_boundaries():
    install_doc = read_doc("README-install.md")
    wiki = read_doc("docs/wiki/README.md")
    boundaries = read_doc("docs/wiki/fail-open-boundaries.md")

    for phrase in (
        "local-process trust",
        "allow_non_loopback",
        "event authentication",
        "TLS or mTLS",
    ):
        assert phrase in install_doc
    assert "fail-open-boundaries.md" in wiki
    for phrase in (
        "可继续",
        "必须失败",
        "event_auth_rejections",
        "未知事件",
        "用户可验证修改",
    ):
        assert phrase in boundaries


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
    assert "process_pid/process_token_hash" in docs
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


def test_changelog_documents_v362_release_notes():
    changelog = read_doc("CHANGELOG.md")
    release_notes = read_doc("docs/release-notes-v3.6.2.md")

    assert "## V3.6.2 — 2026-06-16" in changelog
    assert "issue #53" in changelog
    assert "runtime_import" in changelog
    assert "hook_runtime" in changelog
    assert "Hermes stderr" in changelog
    assert "V3.6.2 Release Notes" in release_notes
    assert "hermes-feishu-card-v3.6.2-macos.tar.gz" in release_notes
    assert "HFC_INSTALL_SPEC" in release_notes


def test_changelog_documents_v363_release_notes():
    changelog = read_doc("CHANGELOG.md")
    release_notes = read_doc("docs/release-notes-v3.6.3.md")

    assert "## V3.6.3 — 2026-06-21" in changelog
    assert "issue #59" in changelog
    assert "issues #56-#59" in release_notes
    assert "_run_agent_inner" in changelog
    assert "interaction_mode" in release_notes
    assert "Telegram" in release_notes
    assert "Windows" in release_notes
    assert "hermes-feishu-card-v3.6.3-macos.tar.gz" in release_notes


def test_changelog_documents_v364_release_notes():
    changelog = read_doc("CHANGELOG.md")
    release_notes = read_doc("docs/release-notes-v3.6.4.md")

    assert "## V3.6.4 — 2026-06-22" in changelog
    assert "issue #61" in changelog
    assert "issue #62" in changelog
    assert "reply_in_thread" in release_notes
    assert 'deliver: "feishu:oc_xxx"' in release_notes
    assert "hermes-feishu-card-v3.6.4-macos.tar.gz" in release_notes


def test_changelog_documents_v365_release_notes():
    changelog = read_doc("CHANGELOG.md")
    release_notes = read_doc("docs/release-notes-v3.6.5.md")

    assert "## V3.6.5 — 2026-06-23" in changelog
    assert "issue #64" in changelog
    assert "issue #65" in changelog
    assert "agent_result.final_response" in release_notes
    assert "_reply_anchor_for_event" in release_notes
    assert "hermes-feishu-card-v3.6.5-macos.tar.gz" in release_notes


def test_changelog_documents_v366_release_notes():
    changelog = read_doc("CHANGELOG.md")
    release_notes = read_doc("docs/release-notes-v3.6.6.md")

    assert "## V3.6.6 — 2026-06-26" in changelog
    assert "issue #67" in changelog
    assert "issue #68" in changelog
    assert "applied" in release_notes
    assert "Hermes CLI reports project" in release_notes
    assert "hermes-feishu-card-v3.6.6-macos.tar.gz" in release_notes


def test_changelog_documents_v370_release_notes():
    changelog = read_doc("CHANGELOG.md")
    release_notes = read_doc("docs/release-notes-v3.7.0.md")

    assert "## V3.7.0 — 2026-06-29" in changelog
    assert "issue #70" in changelog
    assert "install-docker.sh" in release_notes
    assert "docker-compose.example.yml" in release_notes
    assert "hermes-feishu-card-v3.7.0-linux.tar.gz" in release_notes


def test_config_example_documents_profile_and_bot_card_titles():
    config = read_doc("config.yaml.example")

    assert "profiles.<id>.card.title" in config
    assert "bots.items.<id>.card.title" in config
    assert "bot title wins over profile title" in config
    assert "title: Sales Bot" in config
    assert "title: Default Profile" in config
    assert "title: Work Bot" in config
    assert "title: Work Profile" in config
    assert "interaction_mode: auto" in config
    assert "WebSocket card-action path" in config
    assert "explicitly render numbered text choices" in config


def test_docs_describe_card_text_sizes_and_client_controlled_dimensions():
    from hermes_feishu_card.cli import _default_setup_config_text

    config = read_doc("config.yaml.example")
    setup_template = _default_setup_config_text()
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install = read_doc("README-install.md")
    event_flow = read_doc("docs/wiki/event-flow.md")

    for example in (config, setup_template):
        for marker in ("text_sizes:", "body: normal", "footer:", "mobile: notation"):
            assert marker in example
    for doc in (readme, readme_en, install):
        assert "card.text_sizes" in doc
        assert "body" in doc
        assert "footer" in doc
        assert "mobile" in doc
        assert "width/height" in doc
    for marker in (
        "reasoning",
        "tool",
        "notice",
        "heading-0",
        "xxxx-large",
        "normal_v2",
    ):
        assert marker in event_flow


def test_maintainer_docs_define_compaction_visibility_contract():
    event_flow = read_doc("docs/wiki/event-flow.md")
    maintenance = read_doc("docs/wiki/maintenance-guide.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")
    combined = "\n".join((event_flow, maintenance, acceptance))

    for marker in (
        "_status_callback_sync",
        "Compacting context",
        "context-compaction",
        "create_session",
        "status_callback",
    ):
        assert marker in combined
    assert "静默 watchdog" in combined
    assert "百分比" in combined
    assert "真实长会话" in acceptance


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
    assert "思考流更新" in svg
    assert "已完成" in svg
    assert "读取资料" in svg
    assert "生成答案" in svg
    assert "</think>" not in svg
    assert '"thinking"' in preview_json
    assert '"completed"' in preview_json
    assert "思考与工具" in preview_json
    assert "2 次工具调用" in preview_json
    assert "端到端截图" in docs and "e2e-card-preview" in docs


def test_docs_describe_release_readiness_boundaries():
    release_readiness = read_doc("docs/release-readiness.md")
    english_readiness = read_doc("docs/release-readiness.en.md")
    docs = "\n".join(
        [
            read_doc("README.md"),
            release_readiness,
            read_doc("TODO.md"),
        ]
    )

    assert "docs/release-readiness.md" in docs
    assert "4.0.0" in release_readiness
    assert "tool.updated.detail" in release_readiness
    assert "thinking.delta" in release_readiness
    assert "issue #74" in release_readiness
    assert "/hfc" in release_readiness
    assert "Release assets workflow" in release_readiness
    assert "install.ps1" in release_readiness
    assert "install-docker.sh" in release_readiness
    assert "3.1.0" not in release_readiness
    assert "interaction.requested" in release_readiness
    assert "interaction_mode: text" in release_readiness
    assert "append_block" in release_readiness
    assert "MAIN_CONTENT_CHUNK_CHARS" in release_readiness
    assert "doctor --json" in release_readiness
    assert "runtime_import" in release_readiness
    assert "hook failed" in release_readiness
    assert "repair --hermes-dir" in release_readiness
    assert "/health.routing.profiles" in release_readiness
    assert "0.15.x" in release_readiness
    assert "0.17.x" in release_readiness
    assert "0.18.x" in release_readiness
    assert "v2026.7.1+" in release_readiness
    assert "version_source: gateway anchors" in release_readiness
    assert "python3 -m pytest -q" in docs
    assert "真实 Hermes Gateway" in docs
    assert "真实飞书应用" in docs
    assert "App Secret" in docs
    assert "GitHub Actions" in docs

    assert "[English](release-readiness.en.md)" in english_readiness
    assert "4.0.0" in english_readiness
    assert "tool.updated.detail" in english_readiness
    assert "thinking.delta" in english_readiness
    assert "issue #74" in english_readiness
    assert "/hfc" in english_readiness
    assert "install-docker.sh" in english_readiness
    assert "docker-compose.example.yml" in english_readiness
    assert "/opt/hermes" in english_readiness
    assert "/opt/data/config.yaml" in english_readiness
    assert "0.18.x" in english_readiness
    assert "v2026.7.1+" in english_readiness
    assert "version_source: gateway anchors" in english_readiness


def test_v390_documents_operations_reliability_release_gate():
    readme = read_doc("README.md")
    english_readme = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    compose = read_doc("docker-compose.example.yml")
    changelog = read_doc("CHANGELOG.md")
    todo = read_doc("TODO.md")
    guide = read_doc("docs/user-guide.md")
    english_guide = read_doc("docs/user-guide.en.md")
    readiness = read_doc("docs/release-readiness.md")
    english_readiness = read_doc("docs/release-readiness.en.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")
    release_notes = read_doc("docs/release-notes-v3.9.0.md")

    released = re.search(r"(?ms)^## V3\.9\.0 — 2026-07-11\n.*?(?=^## V3\.8\.18|\Z)", changelog).group(0)
    assert "[docs/release-notes-v3.9.0.md](docs/release-notes-v3.9.0.md)" in released
    assert "operations and reliability foundation" in released
    assert "PR #84" in released
    assert "@Zanetach" in released
    assert "## Unreleased" not in changelog
    assert "安全修复" in readme
    assert "profile" in install_doc.lower()
    assert "group" in acceptance.lower()

    assert "v3.8.18" not in compose

    chinese_credit = "卡片 progress-status 路由与 `.env` 白名单扩展的 profile 环境支持"
    english_credit = "card progress-status routing and `.env` allowlist expansion for profile environment support"
    for doc in (readme, guide, todo):
        assert "PR #84" in doc
        assert "@Zanetach" in doc
        assert chinese_credit in doc
    for doc in (english_readme, install_doc, changelog, english_guide, release_notes):
        assert "PR #84" in doc
        assert "@Zanetach" in doc
        assert english_credit in doc

    assert "普通流式卡的 footer/layout 保持不变" in "\n".join((readme, guide))
    assert "normal streaming-card footer/layout remains unchanged" in "\n".join((english_readme, english_guide))

    assert "仅 `doctor` 显示脱敏的完整 identity/profile/event endpoint route chain" in guide
    assert "`status` 只显示运行时的 `last_route` 和各 profile 的 events/profile-source 摘要" in guide
    assert "`/health` 只返回当前 `active_sessions`、`metrics`、`routing` 和 `profile_diagnostics` 等实际字段" in guide
    assert "Only `doctor` shows the complete redacted identity/profile/event-endpoint route chain" in english_guide
    assert "`status` shows only the runtime `last_route` and per-profile events/profile-source summary" in english_guide
    assert "`/health` returns only its current `active_sessions`, `metrics`, `routing`, and `profile_diagnostics` fields" in english_guide
    v390_docs = "\n".join((
        changelog,
        readme,
        english_readme,
        install_doc,
        todo,
        release_notes,
        readiness,
        english_readiness,
        guide,
        english_guide,
        read_doc("docs/wiki/event-flow.md"),
        acceptance,
        read_doc("docs/wiki/maintenance-guide.md"),
    ))
    assert "status/doctor and /health route-chain" not in v390_docs
    assert "`status`、`doctor` 和 `/health` 输出脱敏 route chain" not in v390_docs
    assert "`status`, `doctor`, and `/health` emit redacted route-chain" not in v390_docs

    v390_todo = re.search(r"(?ms)^### V3\.9\.0.*?(?=^### |\Z)", todo).group(0)
    assert "[x] PR #84 / @Zanetach" in v390_todo
    assert chinese_credit in v390_todo
    assert "下次版本候选" not in todo
    assert "当前不单独发版" not in todo

    assert "4 个" in readiness
    assert "four" in english_readiness.lower()
    for asset in (
        "hermes-feishu-card-v3.9.0-macos.tar.gz",
        "hermes-feishu-card-v3.9.0-linux.tar.gz",
        "hermes-feishu-card-v3.9.0-windows.zip",
        "hermes-feishu-card-v3.9.0-checksums.txt",
    ):
        assert asset in release_notes
    assert "Released on 2026-07-11" in release_notes
    assert "release-assets workflow" in release_notes
    assert "Pending release" not in release_notes
    assert "tag has not been created" not in release_notes
    assert "assets have not been created" not in release_notes
    assert "Pending real Feishu acceptance" in release_notes
    assert "已于 2026-07-11 发布" in readiness
    assert "was released on 2026-07-11" in english_readiness
    assert "待验收" in readiness
    assert "pending acceptance" in english_readiness.lower()
    assert "真实 Feishu" in "\\n".join((release_notes, readiness, guide))
    assert "Docker" in "\\n".join((release_notes, readiness, guide))
    assert "real Feishu" in "\\n".join((release_notes, english_readiness, english_guide))
    assert "Docker" in "\\n".join((release_notes, english_readiness, english_guide))
    assert "1172 passed, 3 skipped" in readiness
    assert "1172 passed, 3 skipped" in english_readiness
    assert "已通过（2026-07-11）" in readiness
    assert "Passed on 2026-07-11" in english_readiness
    assert "部分通过" in acceptance
    assert "repair/restart" in readiness
    assert "Pending acceptance" in english_readiness


def test_v391_documents_reliability_hotfix_and_contributors():
    readme = read_doc("README.md")
    english_readme = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    compose = read_doc("docker-compose.example.yml")
    changelog = read_doc("CHANGELOG.md")
    todo = read_doc("TODO.md")
    guide = read_doc("docs/user-guide.md")
    english_guide = read_doc("docs/user-guide.en.md")
    release_notes = read_doc("docs/release-notes-v3.9.1.md")

    assert "## V3.9.1 — 2026-07-11" in changelog
    assert "[docs/release-notes-v3.9.1.md](docs/release-notes-v3.9.1.md)" in changelog
    for reference in ("#82", "#92", "#96", "PR #93", "PR #97", "PR #98"):
        assert reference in release_notes
    for contributor in ("@colinaaa", "@charles5g", "@wjiemin49-ux"):
        assert contributor in release_notes
    assert "marker-only" in release_notes
    assert "source-stripped metadata" in release_notes
    assert "callback" in release_notes.lower()
    assert "footer/layout" in release_notes
    assert "Released on 2026-07-11" in release_notes
    assert "release-assets workflow" in release_notes
    assert "（已发布）" in todo
    assert "已于 2026-07-11 发布" in read_doc("docs/release-readiness.md")
    assert "was released on 2026-07-11" in read_doc("docs/release-readiness.en.md")

    assert "### V3.9.1：可靠性热修" in todo
    assert "v3.9.1" in readme
    assert "v3.9.1" in english_readme
    assert "v3.9.1" in guide
    assert "v3.9.1" in english_guide
    for asset in (
        "hermes-feishu-card-v3.9.1-macos.tar.gz",
        "hermes-feishu-card-v3.9.1-linux.tar.gz",
        "hermes-feishu-card-v3.9.1-windows.zip",
        "hermes-feishu-card-v3.9.1-checksums.txt",
    ):
        assert asset in release_notes


def test_v310_documents_resume_picker_footer_polish_and_contributors():
    readme = read_doc("README.md")
    english_readme = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    compose = read_doc("docker-compose.example.yml")
    changelog = read_doc("CHANGELOG.md")
    todo = read_doc("TODO.md")
    guide = read_doc("docs/user-guide.md")
    english_guide = read_doc("docs/user-guide.en.md")
    event_flow = read_doc("docs/wiki/event-flow.md")
    maintenance = read_doc("docs/wiki/maintenance-guide.md")
    release_notes = read_doc("docs/release-notes-v3.10.0.md")

    assert "## V3.10.0 — 2026-07-11" in changelog
    assert "[docs/release-notes-v3.10.0.md](docs/release-notes-v3.10.0.md)" in changelog
    assert "v3.10.0" in compose or "v3.10.0" in install_doc
    for doc in (readme, english_readme, guide, english_guide):
        assert "v3.10.0" in doc

    for reference in ("#94", "PR #98"):
        assert reference in release_notes
    for contributor in ("@colinaaa", "@charles5g", "jackmim"):
        assert contributor in release_notes
    for phrase in (
        "/resume",
        "select_static",
        "original Hermes",
        "fail-open",
        "footer/layout",
        "HTML escape",
    ):
        assert phrase in release_notes
    assert "group" in release_notes.lower()
    assert "topic" in release_notes.lower()
    assert "resume_picker" in event_flow
    assert "_hfc_original_handle_resume_command" in maintenance
    assert "### V3.10.0：原生会话恢复与轻量视觉增强" in todo

    for asset in (
        "hermes-feishu-card-v3.10.0-macos.tar.gz",
        "hermes-feishu-card-v3.10.0-linux.tar.gz",
        "hermes-feishu-card-v3.10.0-windows.zip",
        "hermes-feishu-card-v3.10.0-checksums.txt",
    ):
        assert asset in release_notes


def test_v400_release_docs_cover_live_runtime_cards():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.0.md")
    notes_en = read_doc("docs/release-notes-v4.0.0.en.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    compose = read_doc("docker-compose.example.yml")
    event_flow = read_doc("docs/wiki/event-flow.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")

    assert "## V4.0.0" in changelog
    assert "tool.updated.detail" in notes
    assert "thinking.delta" in notes
    assert "tool.updated.detail" in notes_en
    assert "thinking.delta" in notes_en
    assert "运行态 Header" in readme
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.0.11}"' in compose
    for doc in (readme, readme_en, install_doc, guide, guide_en):
        assert "HFC_VERSION=v4.0.11" in doc
    for event_name in (
        "progress_callback.preview",
        "tool.updated.detail",
        "thinking.delta",
        "message.completed",
    ):
        assert event_name in event_flow
    for state in ("运行中", "等待用户", "失败", "已完成"):
        assert state in acceptance


def test_v401_release_docs_cover_issue_106_media_text_deduplication():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.1.md")
    notes_en = read_doc("docs/release-notes-v4.0.1.en.md")
    todo = read_doc("TODO.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")

    assert "## V4.0.1 — 2026-07-12" in changelog
    assert "issue #106" in changelog
    assert "Issue #106" in todo
    assert "v4.0.1" in readme
    assert "v4.0.1" in readme_en
    for doc in (notes, notes_en):
        assert "#106" in doc
        assert "MEDIA:" in doc
        assert "@ShakuOvO" in doc
        assert "@blakejia" in doc
        assert "509 passed" in doc
        for asset in (
            "hermes-feishu-card-v4.0.1-macos.tar.gz",
            "hermes-feishu-card-v4.0.1-linux.tar.gz",
            "hermes-feishu-card-v4.0.1-windows.zip",
            "hermes-feishu-card-v4.0.1-checksums.txt",
        ):
            assert asset in doc


def test_v402_release_docs_cover_verified_owned_hook_upgrade():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.2.md")
    notes_en = read_doc("docs/release-notes-v4.0.2.en.md")
    todo = read_doc("TODO.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    config_example = read_doc("config.yaml.example")

    assert "## V4.0.2 — 2026-07-12" in changelog
    assert "owned hook" in changelog
    assert "V4.0.2" in todo
    assert "v4.0.2" in readme
    assert "v4.0.2" in readme_en
    assert "subscription_usage" in config_example
    for doc in (notes, notes_en):
        assert "reapply_current_hook" in doc
        assert "#106" in doc
        assert "#107" in doc
        assert "@ShakuOvO" in doc
        assert "@blakejia" in doc
        assert "@tianqiii" in doc
        assert "subscription_usage" in doc
        assert "121 passed" in doc
        for asset in (
            "hermes-feishu-card-v4.0.2-macos.tar.gz",
            "hermes-feishu-card-v4.0.2-linux.tar.gz",
            "hermes-feishu-card-v4.0.2-windows.zip",
            "hermes-feishu-card-v4.0.2-checksums.txt",
        ):
            assert asset in doc


def test_v403_release_docs_cover_stale_hook_media_text_deduplication():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.3.md")
    notes_en = read_doc("docs/release-notes-v4.0.3.en.md")
    todo = read_doc("TODO.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")

    assert "## V4.0.3 — 2026-07-13" in changelog
    assert "stale-hook" in changelog
    assert "V4.0.3" in todo
    assert "v4.0.3" in readme
    assert "v4.0.3" in readme_en
    for doc in (notes, notes_en):
        assert "#106" in doc
        assert "V4.0.0" in doc
        assert "@ShakuOvO" in doc
        assert "@blakejia" in doc
        assert "513 passed" in doc
        for asset in (
            "hermes-feishu-card-v4.0.3-macos.tar.gz",
            "hermes-feishu-card-v4.0.3-linux.tar.gz",
            "hermes-feishu-card-v4.0.3-windows.zip",
            "hermes-feishu-card-v4.0.3-checksums.txt",
        ):
            assert asset in doc


def test_v404_release_docs_cover_media_literals_and_bound_callbacks():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.4.md")
    notes_en = read_doc("docs/release-notes-v4.0.4.en.md")
    todo = read_doc("TODO.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")

    assert "## V4.0.4 — 2026-07-13" in changelog
    assert "V4.0.4" in todo
    assert "v4.0.4" in readme
    assert "v4.0.4" in readme_en
    for doc in (notes, notes_en):
        assert "#107" in doc
        assert "#110" in doc
        assert "#111" in doc
        assert "#112" in doc
        assert "@sthnow" in doc
        assert "@zkyken" in doc
        assert "@tianqiii" in doc
        assert "404 passed" in doc
        assert "1275 passed, 3 skipped" in doc
        for asset in (
            "hermes-feishu-card-v4.0.4-macos.tar.gz",
            "hermes-feishu-card-v4.0.4-linux.tar.gz",
            "hermes-feishu-card-v4.0.4-windows.zip",
            "hermes-feishu-card-v4.0.4-checksums.txt",
        ):
            assert asset in doc


def test_v405_release_docs_cover_gateway_runtime_version_sync():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.5.md")
    notes_en = read_doc("docs/release-notes-v4.0.5.en.md")
    todo = read_doc("TODO.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")

    assert "## V4.0.5 — 2026-07-13" in changelog
    assert "V4.0.5" in todo
    assert "v4.0.5" in readme
    assert "v4.0.5" in readme_en
    for doc in (notes, notes_en):
        assert "#115" in doc
        assert "PR #116" in doc
        assert "@blakejia" in doc
        assert "3.6.3" in doc
        assert "HFC_INSTALL_SPEC" in doc
        assert "1278 passed, 3 skipped" in doc
        for asset in (
            "hermes-feishu-card-v4.0.5-macos.tar.gz",
            "hermes-feishu-card-v4.0.5-linux.tar.gz",
            "hermes-feishu-card-v4.0.5-windows.zip",
            "hermes-feishu-card-v4.0.5-checksums.txt",
        ):
            assert asset in doc


def test_v406_release_docs_cover_completion_background_and_upgrade_recovery():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.6.md")
    notes_en = read_doc("docs/release-notes-v4.0.6.en.md")
    todo = read_doc("TODO.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")

    assert "## V4.0.6 — 2026-07-15" in changelog
    assert "V4.0.6" in todo
    assert "v4.0.6" in readme
    assert "v4.0.6" in readme_en
    assert "V4.0.6 Hermes 0.18.x" in acceptance
    assert "1315 passed, 3 skipped" in acceptance
    assert "群话题 `/background` 全部通过" in acceptance
    for doc in (notes, notes_en):
        assert "#118" in doc
        assert "#119" in doc
        assert "#120" in doc
        assert "PR #121" in doc
        assert "--accept-hermes-upgrade" in doc
        assert "QUEUED_COMPLETE" in doc
        assert "Background task started" in doc
        assert "@nasvip" in doc
        assert "@hzy" in doc
        assert "@lRoccoon" in doc
        for asset in (
            "hermes-feishu-card-v4.0.6-macos.tar.gz",
            "hermes-feishu-card-v4.0.6-linux.tar.gz",
            "hermes-feishu-card-v4.0.6-windows.zip",
            "hermes-feishu-card-v4.0.6-checksums.txt",
        ):
            assert asset in doc


def test_v407_release_docs_cover_systemd_lifecycle_and_notice_isolation():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.7.md")
    notes_en = read_doc("docs/release-notes-v4.0.7.en.md")
    todo = read_doc("TODO.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")

    assert "## V4.0.7 — 2026-07-16" in changelog
    assert "[docs/release-notes-v4.0.7.md](docs/release-notes-v4.0.7.md)" in changelog
    assert "V4.0.7" in todo
    assert "v4.0.7" in readme
    assert "v4.0.7" in readme_en
    for doc in (readme, readme_en):
        assert "Issue #125" in doc
        assert "PR #124" in doc
        assert "nasvip" in doc
        assert "hzy" in doc
    for doc in (notes, notes_en):
        assert "#125" in doc
        assert "PR #124" in doc
        assert "systemd" in doc
        assert "Restart=on-failure" in doc
        assert "HFC_PYTHON" in doc
        assert "@nasvip" in doc
        assert "@hzy" in doc
        for asset in (
            "hermes-feishu-card-v4.0.7-macos.tar.gz",
            "hermes-feishu-card-v4.0.7-linux.tar.gz",
            "hermes-feishu-card-v4.0.7-windows.zip",
            "hermes-feishu-card-v4.0.7-checksums.txt",
        ):
            assert asset in doc


def test_v408_release_docs_cover_issue_127_cron_native_attachments():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.8.md")
    notes_en = read_doc("docs/release-notes-v4.0.8.en.md")
    todo = read_doc("TODO.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    event_flow = read_doc("docs/wiki/event-flow.md")
    maintenance = read_doc("docs/wiki/maintenance-guide.md")

    assert "## V4.0.8 — 2026-07-16" in changelog
    assert "[docs/release-notes-v4.0.8.md](docs/release-notes-v4.0.8.md)" in changelog
    assert "V4.0.8" in todo
    for doc in (readme, readme_en, guide, guide_en):
        assert "v4.0.8" in doc
        assert "Issue #127" in doc
        assert "zyq2552899783-lgtm" in doc
    for doc in (notes, notes_en):
        assert "#127" in doc
        assert "media_files" in doc
        assert "native_delivery" in doc
        assert "@zyq2552899783-lgtm" in doc
        for asset in (
            "hermes-feishu-card-v4.0.8-macos.tar.gz",
            "hermes-feishu-card-v4.0.8-linux.tar.gz",
            "hermes-feishu-card-v4.0.8-windows.zip",
            "hermes-feishu-card-v4.0.8-checksums.txt",
        ):
            assert asset in doc
    for doc in (event_flow, maintenance):
        assert "media_files" in doc
        assert "native_delivery" in doc


def test_v409_release_docs_cover_issue_130_websocket_handler_stability():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.9.md")
    notes_en = read_doc("docs/release-notes-v4.0.9.en.md")
    todo = read_doc("TODO.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    event_flow = read_doc("docs/wiki/event-flow.md")
    maintenance = read_doc("docs/wiki/maintenance-guide.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")

    assert "## V4.0.9 — 2026-07-16" in changelog
    assert "[docs/release-notes-v4.0.9.md](docs/release-notes-v4.0.9.md)" in changelog
    assert "V4.0.9" in todo
    for doc in (readme, readme_en, guide, guide_en):
        assert "v4.0.9" in doc
        assert "Issue #130" in doc
        assert "Jasonsun77" in doc
    for doc in (notes, notes_en):
        assert "#130" in doc
        assert "EventDispatcherHandler" in doc
        assert "call_soon_threadsafe" in doc
        assert "lark-oapi==1.6.8" in doc
        assert "websockets==15.0.1" in doc
        assert "@Jasonsun77" in doc
        for asset in (
            "hermes-feishu-card-v4.0.9-macos.tar.gz",
            "hermes-feishu-card-v4.0.9-linux.tar.gz",
            "hermes-feishu-card-v4.0.9-windows.zip",
            "hermes-feishu-card-v4.0.9-checksums.txt",
        ):
            assert asset in doc
    for doc in (event_flow, maintenance, acceptance):
        assert "EventDispatcherHandler" in doc
        assert "call_soon_threadsafe" in doc
    assert "lark-oapi==1.6.8" in acceptance
    assert "websockets==15.0.1" in acceptance


def test_v4010_release_candidate_documents_event_transport_security():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.10.md")
    notes_en = read_doc("docs/release-notes-v4.0.10.en.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    todo = read_doc("TODO.md")

    assert "## V4.0.10 — 2026-07-17" in changelog
    assert "[docs/release-notes-v4.0.10.md](docs/release-notes-v4.0.10.md)" in changelog
    for text in (notes, notes_en):
        assert "allow_non_loopback" in text
        assert "HMAC-SHA256" in text
        assert "event_auth_rejections" in text
    assert "docs/release-notes-v4.0.10.md" in readme
    assert "docs/release-notes-v4.0.10.en.md" in readme_en
    assert "V4.0.10" in todo


def test_v4011_release_docs_cover_reliable_notice_delivery():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.11.md")
    notes_en = read_doc("docs/release-notes-v4.0.11.en.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    compose = read_doc("docker-compose.example.yml")
    todo = read_doc("TODO.md")

    assert "## V4.0.11 — 2026-07-18" in changelog
    assert "[docs/release-notes-v4.0.11.md](docs/release-notes-v4.0.11.md)" in changelog
    for text in (notes, notes_en):
        for marker in (
            "delivery_uuid",
            "not_sent",
            "unknown",
            "feishu_send_retries",
            "notice_uncertain_warnings",
            "Issue #135",
        ):
            assert marker in text
        for asset in (
            "hermes-feishu-card-v4.0.11-macos.tar.gz",
            "hermes-feishu-card-v4.0.11-linux.tar.gz",
            "hermes-feishu-card-v4.0.11-windows.zip",
            "hermes-feishu-card-v4.0.11-checksums.txt",
        ):
            assert asset in text
    assert "docs/release-notes-v4.0.11.md" in readme
    assert "docs/release-notes-v4.0.11.en.md" in readme_en
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.0.11}"' in compose
    for doc in (readme, readme_en, install_doc, guide, guide_en):
        assert "HFC_VERSION=v4.0.11" in doc
    assert "V4.0.11" in todo


def test_feishu_cli_playbook_is_linked_and_keeps_cli_optional():
    wiki = read_doc("docs/wiki/README.md")
    playbook = read_doc("docs/wiki/feishu-cli-playbook.md")

    assert "[飞书 CLI 验收与诊断](feishu-cli-playbook.md)" in wiki
    assert "可选" in playbook
    assert "不是 sidecar 运行时依赖" in playbook
    assert "LARK_CLI_NO_PROXY=1" in playbook
    assert "card.action.trigger" in playbook
    assert "不能证明 Hermes 应用" in playbook
    for secret_name in ("token", "callback token", "chat/open/message id"):
        assert secret_name in playbook


def test_v400_real_feishu_state_screenshots_are_published_and_nontrivial():
    docs = {
        "README.md": "docs/assets/",
        "README.en.md": "docs/assets/",
        "docs/user-guide.md": "assets/",
        "docs/user-guide.en.md": "assets/",
        "docs/release-notes-v4.0.0.md": "assets/",
        "docs/release-notes-v4.0.0.en.md": "assets/",
    }
    screenshots = (
        "feishu-v4-runtime-running.png",
        "feishu-v4-runtime-waiting.png",
        "feishu-v4-runtime-failed.png",
        "feishu-v4-runtime-completed.png",
    )

    for doc_path, prefix in docs.items():
        text = read_doc(doc_path)
        for screenshot in screenshots:
            assert f"{prefix}{screenshot}" in text

    for screenshot in screenshots:
        path = ROOT / "docs" / "assets" / screenshot
        assert path.exists()
        assert path.stat().st_size > 20_000


def test_public_v400_plan_does_not_contain_a_real_feishu_chat_id():
    plan = read_doc("docs/superpowers/plans/2026-07-12-v4-live-runtime-card-ux.md")

    assert re.search(r"\boc_[0-9a-f]{32}\b", plan) is None


def test_v400_docs_use_native_reply_as_the_only_completed_header():
    chinese = read_doc("docs/release-notes-v4.0.0.md")
    english = read_doc("docs/release-notes-v4.0.0.en.md")

    assert "只保留飞书原生回复引用作为 Header" in chinese
    assert "不叠加 `Hermes Agent` Card JSON Header" in chinese
    assert "native reply quote as their only Header" in english
    assert "second `Hermes Agent` Card JSON Header" in english


def test_v400_model_picker_matches_hermes_cli_hierarchy():
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    notes = read_doc("docs/release-notes-v4.0.0.md")
    notes_en = read_doc("docs/release-notes-v4.0.0.en.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")
    spec = read_doc(
        "docs/superpowers/specs/2026-07-12-feishu-model-picker-parity-design.md"
    )

    for doc in (readme, guide, notes):
        assert "与 Hermes CLI 使用同一 Provider/模型列表" in doc
        assert "Provider → Model" in doc
    for doc in (readme_en, guide_en, notes_en):
        assert "same Provider/model list as Hermes CLI" in doc
        assert "Provider → Model" in doc
    for phrase in (
        "Provider 数量",
        "模型数量",
        "返回",
        "没有灰色重复消息",
    ):
        assert phrase in acceptance
    for field in ("total_models", "is_current", "Provider → Model"):
        assert field in spec
