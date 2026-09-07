from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def read_doc(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v441_release_notes_and_contributor_attribution():
    notes = read_doc("docs/release-notes-v4.4.1.md")
    notes_en = read_doc("docs/release-notes-v4.4.1.en.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    for text in (notes, notes_en, readme, readme_en):
        for author in ("liooil", "Clarence-G", "mouyong", "Boer2333", "sp960817", "Kevin32623", "shichenshuo-star", "hnzwx", "leavrcn", "micah928"):
            assert f"https://github.com/{author}" in text
    for text in (notes, notes_en):
        assert "reasoning_format: code" in text
        assert "12,000" in text
        assert "Issue #73" in text
        assert "Feishu/Lark" in text
    assert "## V4.4.1 — 2026-09-07" in read_doc("CHANGELOG.md")


def test_current_markers_and_v437_delivery_filter_release_contract():
    pyproject = read_doc("pyproject.toml")
    package = read_doc("hermes_feishu_card/__init__.py")
    config = read_doc("config.yaml.example")
    compose = read_doc("docker-compose.example.yml")
    workflow = read_doc(".github/workflows/tests.yml")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    notes = read_doc("docs/release-notes-v4.3.7.md")
    notes_en = read_doc("docs/release-notes-v4.3.7.en.md")
    notes_v436 = read_doc("docs/release-notes-v4.3.6.md")
    notes_v436_en = read_doc("docs/release-notes-v4.3.6.en.md")
    notes_v435 = read_doc("docs/release-notes-v4.3.5.md")
    notes_v435_en = read_doc("docs/release-notes-v4.3.5.en.md")
    todo = read_doc("TODO.md")
    maintenance = read_doc("docs/wiki/maintenance-guide.md")

    assert 'version = "4.4.1"' in pyproject
    assert '__version__ = "4.4.1"' in package
    assert config.startswith("# Hermes Feishu Streaming Card V4.4.1")
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.4.1}"' in compose
    assert "HFC_VERSION: v4.4.1" in workflow
    assert "当前发布候选为 `4.4.1`" in readiness
    assert "Current release candidate: `4.4.1`" in readiness_en
    for text in (notes_v435, notes_v435_en):
        assert "PR #235" in text
        assert "metadata" in text
        assert "edit_message" in text
        assert "Hermes v2026.8.3" in text
        assert "TypeError" in text
    for text in (notes_v436, notes_v436_en):
        assert "Issue #237" in text
        assert "PR #238" in text
        assert "PR #228" in text
        assert "thread_id" in text
        assert "chat_id" in text
        assert "mentions_in_cards" in text
        assert "completion_notify" in text
        assert "schema 2.0" in text
    for text in (notes, notes_en, readiness, readiness_en):
        assert "Issue #240" in text
        assert "PR #241" in text
        assert "session_key=session_key" in text
        assert "filter_media_delivery_paths" in text
        assert "filter_local_delivery_paths" in text
        assert "exact_delivery_contract: missing_or_unsupported" in text
    assert "V4.3.7" in todo
    assert "唯一 `session_key=session_key` 关键字调用" in maintenance
    assert "apply/remove/restore 逐字往返" in maintenance


def test_v438_setup_sequence_and_proxy_release_contract():
    changelog = read_doc("CHANGELOG.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    maintenance = read_doc("docs/wiki/maintenance-guide.md")
    notes = read_doc("docs/release-notes-v4.3.8.md")
    notes_en = read_doc("docs/release-notes-v4.3.8.en.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    todo = read_doc("TODO.md")

    assert "## V4.3.8 — 2026-08-29" in changelog
    assert "## Unreleased" not in changelog
    for text in (changelog, notes, notes_en, readiness, readiness_en, todo):
        for marker in ("Issue #244", "Issue #245", "PR #242"):
            assert marker in text
    for contributor in ("@nasvip", "@Timeral", "@PureWhiteWu"):
        assert contributor in changelog
    for text in (readme, readme_en):
        for contributor in ("nasvip", "Timeral", "PureWhiteWu"):
            assert f"https://github.com/{contributor}" in text
    assert "docs/release-notes-v4.3.8.md" in readme
    assert "docs/release-notes-v4.3.8.en.md" in readme_en
    for text in (readme, readme_en, install_doc, guide, guide_en, maintenance):
        assert "--transient" in text
    assert "重启后不会存活" in guide
    assert "will not survive a reboot" in install_doc
    assert "will not survive a reboot" in guide_en
    assert "不得推进 Hermes `/events` transport 的 `last_sequence`" in maintenance
    assert "callback 响应卡要在同一 session lock 内快照" in maintenance
    assert "不得自动 enable linger、调用 sudo 或进入 system manager" in maintenance
    for text in (notes, notes_en, readiness, readiness_en):
        assert "3343 passed, 6 skipped" in text
        assert "systemd" in text
    assert "真实 Feishu/Lark 客户端 smoke：**未执行**" in notes
    assert "Real Feishu/Lark client smoke: **not run**" in notes_en


def test_v440_current_hermes_capability_center_release_contract():
    changelog = read_doc("CHANGELOG.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    notes = read_doc("docs/release-notes-v4.4.0.md")
    notes_en = read_doc("docs/release-notes-v4.4.0.en.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    design = read_doc(
        "docs/superpowers/specs/2026-08-31-v4.4.0-hermes-native-command-center-design.md"
    )
    todo = read_doc("TODO.md")

    assert "## V4.4.0 — 2026-08-31" in changelog
    assert "docs/release-notes-v4.4.0.md" in readme
    assert "docs/release-notes-v4.4.0.en.md" in readme_en
    assert "## V4.4.0 新版 Hermes 原生能力中心" in guide
    assert "## V4.4.0 Native Capability Center for Current Hermes" in guide_en
    assert "## V4.4.0 发布门禁" in readiness
    assert "## V4.4.0 Release Gates" in readiness_en
    assert "### V4.4.0：新版 Hermes 原生能力中心与可视化交互" in todo
    for text in (changelog, notes, notes_en, readiness, readiness_en, design):
        for marker in (
            "v2026.8.27",
            "0.20.6",
            "COMMAND_REGISTRY",
            "/bg",
            "/btw",
            "/plan",
            "busy",
        ):
            assert marker in text
    for text in (notes, notes_en, design):
        for marker in ("update_queue_peak", "Markdown", "66"):
            assert marker in text
    assert "能力中心" in notes
    assert "能力中心" in design
    assert "capability center" in notes_en
    assert "复制原入站 `MessageEvent`" in design
    assert "copies the original `MessageEvent`" in notes_en


def test_maintainer_docs_define_reliable_notice_delivery_contract():
    event_flow = read_doc("docs/wiki/event-flow.md")
    maintenance = read_doc("docs/wiki/maintenance-guide.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")
    combined = "\n".join((event_flow, maintenance, acceptance))

    for marker in (
        "delivery_uuid",
        "accepted",
        "not_sent",
        "unknown",
        "feishu_send_retries",
        "feishu_send_unknown_outcomes",
        "feishu_noop_attempts",
        "notice_native_fallbacks",
        "notice_uncertain_warnings",
        "notice_update_failures",
        "status_code",
        "api_code",
    ):
        assert marker in combined
    assert "不重试 `/events`" in combined
    assert "原始通知文本" in combined
    assert "不重复原始通知文本" in combined
    assert "⚠️ 一条运行提示的卡片投递结果无法确认，请稍后查看 /hfc status。" in combined


def test_readme_documents_sidecar_only_and_supported_hermes_version():
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
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
    assert "docs/assets/feishu-topic-card-showcase-v389.png" not in readme
    assert "docs/assets/feishu-topic-card-showcase-v389.png" not in readme_en
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
    assert (
        "</p>\n\n"
        "![Hermes Feishu Streaming Card 封面](docs/assets/readme-cover.png)"
    ) in readme
    # Contributor history is intentionally retained across releases; keep the
    # top-level README bounded without deleting earlier-version credits.
    assert len(readme.splitlines()) <= 320


def test_readmes_preserve_historical_pr_issue_and_commit_credits():
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    guide = read_doc("docs/user-guide.md")
    agents = read_doc("AGENTS.md")

    graph = "https://github.com/baileyh8/hermes-feishu-streaming-card/graphs/contributors"
    assert graph in readme
    assert graph in readme_en
    for handle in (
        "wzgrx",
        "zsfjim",
        "atop0914",
        "0269chaoup",
        "dominofeng-maker",
        "coder-zhw",
        "x-giraffee",
        "jackwude",
        "bestkxt",
        "Thomas0x1f",
        "tianxia3111",
        "ati121",
        "Cyber-Yichen",
        "wholegale39",
        "dake6767",
        "foras910521-lab",
        "simon881",
        "mslchy",
        "createpjf",
        "xingdongcai",
        "Crystalxd",
        "jdysya",
        "AnyNice",
        "Timeral",
        "chinakids",
        "yuqianma",
        "leavrcn",
        "jsuper",
        "mouyong",
        "L261173157",
        "saulgoodmanngabriel",
        "zhangzq",
        "RanHuang",
        "Akes119",
        "yaoge103",
    ):
        link = f"https://github.com/{handle}"
        assert link in readme
        assert link in readme_en

    for marker in (
        "full tag/release history",
        "merged or materially absorbed PRs",
        "accepted issue evidence",
        "Co-authored-by",
        "Never fabricate",
    ):
        assert marker in agents
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
    assert 'NAME="hermes-feishu-card-${RELEASE_TAG}"' in workflow
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

    assert "## V3.8 / V3.9 / V3.10 / V4 系列路线" in todo
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


def test_event_protocol_documents_optional_turn_id_and_legacy_fallback():
    protocol = read_doc("docs/event-protocol.md")
    protocol_en = read_doc("docs/event-protocol.en.md")
    event_flow = read_doc("docs/wiki/event-flow.md")
    maintenance = read_doc("docs/wiki/maintenance-guide.md")

    for text in (protocol, protocol_en):
        for marker in ("turn_id", "message_id", "reply_to_message_id"):
            assert marker in text
    assert "`turn_id` 是可选字段" in protocol
    assert "缺少 `turn_id` 时" in protocol
    assert "`turn_id` is optional" in protocol_en
    assert "When `turn_id` is absent" in protocol_en

    for text in (event_flow, maintenance):
        assert "canonical turn hard fence" in text
        assert "turn_id" in text
        assert "reply_to_message_id" in text


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
        assert "Windows non-loopback" in doc
        assert "ACL privacy" in doc
    for doc in (readme, guide, architecture):
        assert "Windows non-loopback" in doc
        assert "ACL 私有性" in doc
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
    assert changelog.index("## V4.3.8") < changelog.index("## V4.3.7")
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
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.4.1}"' in compose
    for doc in (readme, readme_en, install_doc, guide, guide_en):
        assert "HFC_VERSION=v4.4.1" in doc
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
    assert "V4.0.11" in todo


def test_v4012_release_docs_cover_compaction_text_sizes_and_noop_credentials():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.12.md")
    notes_en = read_doc("docs/release-notes-v4.0.12.en.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    compose = read_doc("docker-compose.example.yml")
    todo = read_doc("TODO.md")

    assert "## V4.0.12 — 2026-07-18" in changelog
    assert "[docs/release-notes-v4.0.12.md](docs/release-notes-v4.0.12.md)" in changelog
    for text in (notes, notes_en):
        for marker in (
            "Issue #133",
            "Issue #136",
            "context-compaction",
            "text_sizes",
            "--env-file",
            "noop_mode",
            "feishu_noop_attempts",
            "not_sent",
        ):
            assert marker in text
        for asset in (
            "hermes-feishu-card-v4.0.12-macos.tar.gz",
            "hermes-feishu-card-v4.0.12-linux.tar.gz",
            "hermes-feishu-card-v4.0.12-windows.zip",
            "hermes-feishu-card-v4.0.12-checksums.txt",
        ):
            assert asset in text
    assert "docs/release-notes-v4.0.12.md" in readme
    assert "docs/release-notes-v4.0.12.en.md" in readme_en
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.4.1}"' in compose
    for doc in (readme, readme_en, install_doc, guide, guide_en):
        assert "HFC_VERSION=v4.4.1" in doc
    assert "V4.0.12" in todo


def test_v4013_release_docs_cover_all_command_feedback_cards():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.13.md")
    notes_en = read_doc("docs/release-notes-v4.0.13.en.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    compose = read_doc("docker-compose.example.yml")
    todo = read_doc("TODO.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")

    assert "## V4.0.13 — 2026-07-20" in changelog
    assert "[docs/release-notes-v4.0.13.md](docs/release-notes-v4.0.13.md)" in changelog
    for text in (notes, notes_en):
        for marker in (
            "plugin/quick",
            "unknown command",
            "/compress",
            "/model",
            "/resume",
            "/update",
            "system.notice",
            "1482 passed, 4 skipped",
        ):
            assert marker in text
        for asset in (
            "hermes-feishu-card-v4.0.13-macos.tar.gz",
            "hermes-feishu-card-v4.0.13-linux.tar.gz",
            "hermes-feishu-card-v4.0.13-windows.zip",
            "hermes-feishu-card-v4.0.13-checksums.txt",
        ):
            assert asset in text
    assert "docs/release-notes-v4.0.13.md" in readme
    assert "docs/release-notes-v4.0.13.en.md" in readme_en
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.4.1}"' in compose
    for doc in (readme, readme_en, install_doc, guide, guide_en):
        assert "HFC_VERSION=v4.4.1" in doc
    assert "V4.0.13" in todo
    assert "V4.0.13 发布门禁" in readiness
    assert "V4.0.13 Release Gates" in readiness_en


def test_v4014_release_docs_cover_long_running_heartbeat_fix():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.14.md")
    notes_en = read_doc("docs/release-notes-v4.0.14.en.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    compose = read_doc("docker-compose.example.yml")
    todo = read_doc("TODO.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    event_flow = read_doc("docs/wiki/event-flow.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")

    assert "## V4.0.14 — 2026-07-20" in changelog
    assert "[docs/release-notes-v4.0.14.md](docs/release-notes-v4.0.14.md)" in changelog
    for text in (notes, notes_en):
        for marker in (
            "Issue #142",
            "heartbeat",
            "non-terminal",
            "message.completed",
            "unknown",
            "@ati121",
        ):
            assert marker in text
        for asset in (
            "hermes-feishu-card-v4.0.14-macos.tar.gz",
            "hermes-feishu-card-v4.0.14-linux.tar.gz",
            "hermes-feishu-card-v4.0.14-windows.zip",
            "hermes-feishu-card-v4.0.14-checksums.txt",
        ):
            assert asset in text
    assert "docs/release-notes-v4.0.14.md" in readme
    assert "docs/release-notes-v4.0.14.en.md" in readme_en
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.4.1}"' in compose
    for doc in (readme, readme_en, install_doc, guide, guide_en):
        assert "HFC_VERSION=v4.4.1" in doc
    assert "V4.0.14" in todo
    assert "V4.0.14 发布门禁" in readiness
    assert "V4.0.14 Release Gates" in readiness_en
    assert "原始用户消息锚点" in event_flow
    assert "V4.0.14 长任务 heartbeat 热修" in acceptance


def test_v4015_release_docs_cover_tool_timeline_and_upgrade_guard():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.15.md")
    notes_en = read_doc("docs/release-notes-v4.0.15.en.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    compose = read_doc("docker-compose.example.yml")
    todo = read_doc("TODO.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    event_flow = read_doc("docs/wiki/event-flow.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")

    assert "## V4.0.15 — 2026-07-22" in changelog
    assert "[docs/release-notes-v4.0.15.md](docs/release-notes-v4.0.15.md)" in changelog
    for text in (notes, notes_en):
        for marker in (
            "Issue #141",
            "FlushController",
            "upgrade_repair_required",
            "manual_review_required",
            "deepseek-v4-flash",
        ):
            assert marker in text
        for asset in (
            "hermes-feishu-card-v4.0.15-macos.tar.gz",
            "hermes-feishu-card-v4.0.15-linux.tar.gz",
            "hermes-feishu-card-v4.0.15-windows.zip",
            "hermes-feishu-card-v4.0.15-checksums.txt",
        ):
            assert asset in text
    assert "docs/release-notes-v4.0.15.md" in readme
    assert "docs/release-notes-v4.0.15.en.md" in readme_en
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.4.1}"' in compose
    for doc in (readme, readme_en, install_doc, guide, guide_en):
        assert "HFC_VERSION=v4.4.1" in doc
    assert "V4.0.15" in todo
    assert "V4.0.15 发布门禁" in readiness
    assert "V4.0.15 Release Gates" in readiness_en
    assert "正在加载上下文" in event_flow
    assert "V4.0.15 工具事件视觉与加载动画" in acceptance


def test_v4016_release_docs_cover_loading_dedup_and_real_tool_duration():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.16.md")
    notes_en = read_doc("docs/release-notes-v4.0.16.en.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    compose = read_doc("docker-compose.example.yml")
    todo = read_doc("TODO.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")

    assert "## V4.0.16 — 2026-07-22" in changelog
    assert "[docs/release-notes-v4.0.16.md](docs/release-notes-v4.0.16.md)" in changelog
    for text in (notes, notes_en):
        for marker in (
            "kwargs.duration",
            "duration_ms",
            "terminal-only",
            "1504 passed, 4 skipped",
        ):
            assert marker in text
        for asset in (
            "hermes-feishu-card-v4.0.16-macos.tar.gz",
            "hermes-feishu-card-v4.0.16-linux.tar.gz",
            "hermes-feishu-card-v4.0.16-windows.zip",
            "hermes-feishu-card-v4.0.16-checksums.txt",
        ):
            assert asset in text
    assert "docs/release-notes-v4.0.16.md" in readme
    assert "docs/release-notes-v4.0.16.en.md" in readme_en
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.4.1}"' in compose
    for doc in (readme, readme_en, install_doc, guide, guide_en):
        assert "HFC_VERSION=v4.4.1" in doc
    assert "V4.0.16" in todo
    assert "V4.0.16 发布门禁" in readiness
    assert "V4.0.16 Release Gates" in readiness_en
    assert "V4.0.16 加载态去重与真实工具耗时" in acceptance


def test_v4017_release_docs_cover_parallel_tool_correlation():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.17.md")
    notes_en = read_doc("docs/release-notes-v4.0.17.en.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    compose = read_doc("docker-compose.example.yml")
    todo = read_doc("TODO.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")

    assert "## V4.0.17 — 2026-07-22" in changelog
    assert "[docs/release-notes-v4.0.17.md](docs/release-notes-v4.0.17.md)" in changelog
    for text in (notes, notes_en):
        for marker in (
            "tool_start_callback",
            "tool_complete_callback",
            "call_id",
            "1508 passed, 4 skipped",
        ):
            assert marker in text
        for asset in (
            "hermes-feishu-card-v4.0.17-macos.tar.gz",
            "hermes-feishu-card-v4.0.17-linux.tar.gz",
            "hermes-feishu-card-v4.0.17-windows.zip",
            "hermes-feishu-card-v4.0.17-checksums.txt",
        ):
            assert asset in text
    assert "docs/release-notes-v4.0.17.md" in readme
    assert "docs/release-notes-v4.0.17.en.md" in readme_en
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.4.1}"' in compose
    for doc in (readme, readme_en, install_doc, guide, guide_en):
        assert "HFC_VERSION=v4.4.1" in doc
    assert "V4.0.17" in todo
    assert "V4.0.17 发布门禁" in readiness
    assert "V4.0.17 Release Gates" in readiness_en
    assert "V4.0.17 并行同名工具事件关联" in acceptance


def test_v4018_release_docs_cover_feishu_sdk_capability_guard():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.18.md")
    notes_en = read_doc("docs/release-notes-v4.0.18.en.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    compose = read_doc("docker-compose.example.yml")
    todo = read_doc("TODO.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")

    assert "## V4.0.18 — 2026-07-22" in changelog
    assert "[docs/release-notes-v4.0.18.md](docs/release-notes-v4.0.18.md)" in changelog
    for text in (notes, notes_en):
        for marker in (
            "extra_ua_tags",
            "feishu_sdk_incompatible",
            "lark-oapi==1.6.8",
            "1511 passed, 4 skipped",
        ):
            assert marker in text
        for asset in (
            "hermes-feishu-card-v4.0.18-macos.tar.gz",
            "hermes-feishu-card-v4.0.18-linux.tar.gz",
            "hermes-feishu-card-v4.0.18-windows.zip",
            "hermes-feishu-card-v4.0.18-checksums.txt",
        ):
            assert asset in text
    assert "docs/release-notes-v4.0.18.md" in readme
    assert "docs/release-notes-v4.0.18.en.md" in readme_en
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.4.1}"' in compose
    for doc in (readme, readme_en, install_doc, guide, guide_en):
        assert "HFC_VERSION=v4.4.1" in doc
    assert "V4.0.18" in todo
    assert "V4.0.18 发布门禁" in readiness
    assert "V4.0.18 Release Gates" in readiness_en
    assert "V4.0.18 Hermes Feishu SDK 兼容门禁" in acceptance


def test_v4019_release_docs_cover_venv_pip_install_guard():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.19.md")
    notes_en = read_doc("docs/release-notes-v4.0.19.en.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    compose = read_doc("docker-compose.example.yml")
    todo = read_doc("TODO.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")

    assert "## V4.0.19 — 2026-07-22" in changelog
    assert "[docs/release-notes-v4.0.19.md](docs/release-notes-v4.0.19.md)" in changelog
    for text in (notes, notes_en):
        for marker in ("pip --user", "HFC_PIP_USER", "1513 passed, 4 skipped"):
            assert marker in text
        for asset in (
            "hermes-feishu-card-v4.0.19-macos.tar.gz",
            "hermes-feishu-card-v4.0.19-linux.tar.gz",
            "hermes-feishu-card-v4.0.19-windows.zip",
            "hermes-feishu-card-v4.0.19-checksums.txt",
        ):
            assert asset in text
    assert "docs/release-notes-v4.0.19.md" in readme
    assert "docs/release-notes-v4.0.19.en.md" in readme_en
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.4.1}"' in compose
    for doc in (readme, readme_en, install_doc, guide, guide_en):
        assert "HFC_VERSION=v4.4.1" in doc
    assert "V4.0.19" in todo
    assert "V4.0.19 发布门禁" in readiness
    assert "V4.0.19 Release Gates" in readiness_en


def test_v4020_release_docs_cover_notice_accepted_ack_and_observability():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.20.md")
    notes_en = read_doc("docs/release-notes-v4.0.20.en.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    compose = read_doc("docker-compose.example.yml")
    todo = read_doc("TODO.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")

    assert "## V4.0.20 — 2026-07-22" in changelog
    assert "[docs/release-notes-v4.0.20.md](docs/release-notes-v4.0.20.md)" in changelog
    for text in (notes, notes_en):
        for marker in (
            "accepted",
            "applied=true",
            "notice_update_failures",
            "status_code",
            "api_code",
            "1517 passed, 4 skipped",
        ):
            assert marker in text
        for asset in (
            "hermes-feishu-card-v4.0.20-macos.tar.gz",
            "hermes-feishu-card-v4.0.20-linux.tar.gz",
            "hermes-feishu-card-v4.0.20-windows.zip",
            "hermes-feishu-card-v4.0.20-checksums.txt",
        ):
            assert asset in text
    assert "docs/release-notes-v4.0.20.md" in readme
    assert "docs/release-notes-v4.0.20.en.md" in readme_en
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.4.1}"' in compose
    for doc in (readme, readme_en, install_doc, guide, guide_en):
        assert "HFC_VERSION=v4.4.1" in doc
    assert "V4.0.20" in todo
    assert "V4.0.20 发布门禁" in readiness
    assert "V4.0.20 Release Gates" in readiness_en
    assert "V4.0.20 notice 异步 ACK 语义" in acceptance


def test_v4021_release_docs_record_content_integrity_and_real_feishu_acceptance():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.21.md")
    notes_en = read_doc("docs/release-notes-v4.0.21.en.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    testing = read_doc("docs/testing.md")
    testing_en = read_doc("docs/testing.en.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    compose = read_doc("docker-compose.example.yml")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    todo = read_doc("TODO.md")

    assert "## V4.0.21 — 2026-07-28" in changelog
    assert "[docs/release-notes-v4.0.21.md](docs/release-notes-v4.0.21.md)" in changelog
    for text in (notes, notes_en):
        for marker in (
            "Issue #155",
            "answer -> tool",
            "tool -> answer -> completed",
            "Issue #147",
            "native image",
            "accepted",
            "uncertain-delivery warning",
        ):
            assert marker in text
    assert "不改变卡片 UI 或配置" in notes
    assert "does not change the card UI or configuration" in notes_en
    assert "2026-07-28 真实飞书验收" in notes
    assert "Real Feishu acceptance on 2026-07-28" in notes_en
    assert "1 条 native image" in notes
    assert "one native image" in notes_en
    assert "23/23" in notes
    assert "23/23" in notes_en
    assert "1 次发送成功、16 次更新成功" in notes
    assert "1 send success and 16 update successes" in notes_en
    assert "site-packages 中的候选 runtime 为 4.0.21" in notes
    assert "site-packages was 4.0.21" in notes_en
    assert "不宣称截图或桌面/移动端视觉 QA" in notes
    assert "does not claim screenshot or desktop/mobile visual QA" in notes_en
    assert "公开 tagged installer 与 Release assets 仍待 post-tag 验证" in notes
    assert "public tagged installer and Release assets remain pending post-tag verification" in notes_en
    assert "docs/release-notes-v4.0.21.md" in readme
    assert "docs/release-notes-v4.0.21.en.md" in readme_en
    assert "test_prepare_completed_answer_issue155.py" in testing
    assert "test_prepare_completed_answer_issue155.py" in testing_en
    assert "test_v4021_hook_runtime_keeps_image_delivery_and_accepted_notice_in_same_turn" in testing
    assert "test_v4021_hook_runtime_keeps_image_delivery_and_accepted_notice_in_same_turn" in testing_en
    assert "V4.0.21 内容完整性与媒体/notice 组合验收" in acceptance
    assert "2026-07-28 真实验收结果" in acceptance
    assert "23/23" in acceptance
    assert "site-packages 中的候选 runtime 为 4.0.21" in acceptance
    assert "不宣称截图或桌面/移动端视觉 QA" in acceptance

    assert 'HFC_VERSION: "${HFC_VERSION:-v4.4.1}"' in compose
    for doc in (readme, readme_en, install_doc, guide, guide_en):
        assert "HFC_VERSION=v4.4.1" in doc
    assert "`v4.4.1`（Compose 示例）" in guide
    assert "The Compose example defaults `HFC_VERSION` to `v4.4.1`." in guide_en
    for doc in (guide, guide_en):
        assert re.search(
            r"(?:Compose|Compose 示例).*v4\.0\.(?:0|[1-9]|1[0-9]|20)(?!\d)"
            r"|v4\.0\.(?:0|[1-9]|1[0-9]|20)(?!\d).*?(?:Compose|Compose 示例)",
            doc,
            re.IGNORECASE,
        ) is None
    assert "| [v4.0.21](release-notes-v4.0.21.md) | 2026-07-28 |" in guide
    assert "| [v4.0.21](release-notes-v4.0.21.en.md) | 2026-07-28 |" in guide_en
    assert "当前发布候选为 `4.4.1`" in readiness
    assert "Current release candidate: `4.4.1`" in readiness_en
    assert "## V4.0.21 发布门禁" in readiness
    assert "## V4.0.21 Release Gates" in readiness_en
    assert "真实飞书图片验收：**已通过（2026-07-28）**" in readiness
    assert "Real Feishu image acceptance: **passed (2026-07-28)**" in readiness_en
    assert "23/23" in readiness
    assert "23/23" in readiness_en
    assert "公开 tagged installer 与 Release assets 的 post-tag 验证仍待完成" in readiness
    assert "public tagged installer and Release-asset post-tag verification remain pending" in readiness_en
    assert "## V4.0.20 发布门禁" in readiness
    assert "## V4.0.20 Release Gates" in readiness_en
    assert "V4.0.21" in todo
    assert "### V4.0.21：内容完整性与图片/notice 组合热修（发布候选）" in todo
    assert "真实飞书图片验收已通过（2026-07-28）" in todo

    v4021_acceptance = re.search(
        r"(?ms)^## V4\.0\.21.*?(?=^## V4\.0\.20|\Z)", acceptance
    ).group(0)
    v4021_readiness = re.search(
        r"(?ms)^## V4\.0\.21.*?(?=^## V4\.0\.20|\Z)", readiness
    ).group(0)
    v4021_readiness_en = re.search(
        r"(?ms)^## V4\.0\.21.*?(?=^## V4\.0\.20|\Z)", readiness_en
    ).group(0)
    v4021_todo = re.search(r"(?ms)^### V4\.0\.21.*?(?=^### |\Z)", todo).group(0)
    for text in (notes, v4021_acceptance, v4021_readiness, v4021_todo):
        for unsupported_detail in (
            "本机候选版",
            "真实 Hermes 配置模型",
            "`/background`",
            "只读 terminal",
            "各至少 180 中文字符",
            "两个标记同在",
            "官方 install",
        ):
            assert unsupported_detail not in text
    for text in (notes_en, v4021_readiness_en):
        for unsupported_detail in (
            "local candidate",
            "configured real Hermes model",
            "`/background`",
            "read-only terminal",
            "at least 180 Chinese characters",
            "both markers appeared",
            "official install",
        ):
            assert unsupported_detail not in text

    v4021_acceptance_docs = "\n".join(
        (notes, notes_en, testing, testing_en, acceptance, readiness, readiness_en, todo, guide, guide_en)
    )
    assert "真实飞书图片 smoke 尚未完成" not in v4021_acceptance_docs
    assert "real Feishu image smoke remains pending" not in v4021_acceptance_docs

    public_v4021_docs = "\n".join((notes, notes_en, testing, testing_en, acceptance))
    assert re.search(r"\b(?:oc|om|ou)_[0-9a-f]{16,}\b", public_v4021_docs) is None
    assert re.search(
        r"FEISHU_APP_SECRET=(?!xxx\b)[^\s]+", public_v4021_docs
    ) is None


def test_v4021_docs_record_final_local_release_gate():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.21.md")
    notes_en = read_doc("docs/release-notes-v4.0.21.en.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    todo = read_doc("TODO.md")

    gate_markers = (
        "1526 passed, 4 skipped in 53.56s",
        "uv build",
        "hermes_feishu_streaming_card-4.0.21.tar.gz",
        "hermes_feishu_streaming_card-4.0.21-py3-none-any.whl",
        "Python 3.12",
        "site-packages",
        "4.0.21",
        "hermes-feishu-card = hermes_feishu_card.cli:main",
        "--help exit 0",
    )
    for text in (notes, notes_en, readiness, readiness_en):
        for marker in gate_markers:
            assert marker in text

    assert "1526 passed, 4 skipped in 53.56s" in changelog
    assert "uv build" in changelog
    assert "hermes_feishu_streaming_card-4.0.21-py3-none-any.whl" in changelog
    assert "最终本地发布门禁已通过" in todo
    assert "公开 tagged installer 与 Release assets 的 post-tag 验证仍待完成" in todo
    for text in (changelog, notes, notes_en, readiness, readiness_en, todo):
        assert "/private/tmp" not in text
        assert "/Users/" not in text


def test_v410_release_docs_cover_native_policy_limits_integrity_and_services():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.1.0.md")
    notes_en = read_doc("docs/release-notes-v4.1.0.en.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    migration = read_doc("docs/migration.md")
    migration_en = read_doc("docs/migration.en.md")
    safety = read_doc("docs/installer-safety.md")
    safety_en = read_doc("docs/installer-safety.en.md")
    architecture = read_doc("docs/architecture.md")
    architecture_en = read_doc("docs/architecture.en.md")
    event_protocol = read_doc("docs/event-protocol.md")
    event_protocol_en = read_doc("docs/event-protocol.en.md")
    wiki = read_doc("docs/wiki/README.md")
    controls = read_doc("docs/wiki/v4.1-safety-controls.md")
    event_flow = read_doc("docs/wiki/event-flow.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")
    config = read_doc("config.yaml.example")
    compose = read_doc("docker-compose.example.yml")

    assert "## V4.1.0 — 2026-07-28" in changelog
    assert "[docs/release-notes-v4.1.0.md](docs/release-notes-v4.1.0.md)" in changelog
    assert "docs/release-notes-v4.1.0.md" in readme
    assert "docs/release-notes-v4.1.0.en.md" in readme_en
    assert "[V4.1 安全控制与排障](v4.1-safety-controls.md)" in wiki
    assert "## V4.1.0 安全控制验收" in acceptance

    bilingual = (notes, notes_en, guide, guide_en, migration, migration_en, controls)
    for text in bilingual:
        for marker in (
            "bindings.native_chats",
            "chats use-native",
            "chats use-card",
            "table_overflow_mode",
            "compact",
            "truncate",
            "28,000",
            "integrity",
            "migrate-safe",
            "service.manager",
            "systemd-user",
            "systemd-system",
            "detached",
        ):
            assert marker in text

    for text in (notes, notes_en, migration, migration_en, safety, safety_en, controls):
        assert "sidecar.restart_required" in text
        assert "gateway.restart_required" in text
        assert "runtime.hello" in text
        assert "runtime.heartbeat" in text

    for text in (notes, notes_en, install_doc, controls):
        assert "auto" in text
        assert "Docker" in text
        assert "sudo" in text

    for text in (notes, notes_en):
        for marker in (
            "#157",
            "shutdown-awa",
            "#158",
            "Jasonsun77",
            "#159",
            "Redeemer-w",
            "#156",
            "Cyber-Yichen",
            "#160",
            "wholegale39",
        ):
            assert marker in text

    assert "hfc-policy-v1" in event_protocol
    assert "hfc-policy-v1" in event_protocol_en
    assert "hfc-runtime-v1" in event_protocol
    assert "hfc-runtime-v1" in event_protocol_en
    assert "POST /delivery/policy" in architecture
    assert "POST /delivery/policy" in architecture_en
    assert "POST /runtime/events" in architecture
    assert "POST /runtime/events" in architecture_en

    assert "native_chats: []" in config
    assert "table_overflow_mode: compact" in config
    assert "mode: safe" in config
    assert "manager: auto" in config
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.4.1}"' in compose
    for doc in (readme, readme_en, install_doc, guide, guide_en):
        assert "HFC_VERSION=v4.4.1" in doc
    for doc in (notes, notes_en):
        assert "HFC_VERSION=v4.1.0" in doc

    public_docs = "\n".join(
        (
            changelog,
            notes,
            notes_en,
            readme,
            readme_en,
            install_doc,
            guide,
            guide_en,
            migration,
            migration_en,
            safety,
            safety_en,
            architecture,
            architecture_en,
            event_protocol,
            event_protocol_en,
            controls,
            acceptance,
        )
    )
    assert "/private/tmp" not in public_docs
    assert "/Users/" not in public_docs
    assert re.search(r"\b(?:oc|om|ou)_[0-9a-f]{16,}\b", public_docs) is None
    assert re.search(r"FEISHU_APP_SECRET=(?!xxx\b)[^\s]+", public_docs) is None


def test_v411_release_docs_define_upgrade_recovery_safety_contract():
    changelog = read_doc("CHANGELOG.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    notes = read_doc("docs/release-notes-v4.1.1.md")
    notes_en = read_doc("docs/release-notes-v4.1.1.en.md")
    controls = read_doc("docs/wiki/v4.1-safety-controls.md")
    event_flow = read_doc("docs/wiki/event-flow.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")
    workflow = read_doc(".github/workflows/tests.yml")
    todo = read_doc("TODO.md")

    assert "## V4.1.1" in changelog
    assert "docs/release-notes-v4.1.1.md" in changelog
    assert "docs/release-notes-v4.1.1.md" in readme
    assert "docs/release-notes-v4.1.1.en.md" in readme_en
    assert "HFC_VERSION: v4.4.1" in workflow
    assert "### V4.1.1：升级恢复安全热修（已发布）" in todo
    assert "### V4.1.0：投递策略与运行安全（已发布）" in todo

    for text in (notes, notes_en):
        for marker in (
            "runtime_heartbeat_waiting",
            "runtime_heartbeat_missing",
            "integrity acknowledge-review",
            "--state-dir",
            "0644",
            "0600",
            "pidfile",
            "Python identity",
            "target-bound",
            "CAS",
            "token",
            "python -I",
            "--env-file",
            "HFC_VERSION=v4.1.1",
        ):
            assert marker in text

    for text in (controls, event_flow):
        assert "runtime.hello" in text
        assert "runtime.heartbeat" in text
        assert "acknowledge-review" in text
        assert "pidfile-less" in text

    assert "heartbeat" in acceptance
    assert "review acknowledgement" in acceptance
    assert "pidfile-less" in acceptance
    assert "PID/PGID" in acceptance
    assert "待验收" in acceptance


def test_docs_define_verified_integrity_acknowledgement_boundary():
    migration = read_doc("docs/migration.md")
    migration_en = read_doc("docs/migration.en.md")
    controls = read_doc("docs/wiki/v4.1-safety-controls.md")

    for text in (migration, migration_en, controls):
        assert "acknowledge-review" in text
        assert "recovery_not_required" in text
    for text in (migration, controls):
        assert "其他 manual-review reason 必须先修复" in text
        assert "重新运行 doctor" in text
    assert "Every other manual-review reason must be repaired" in migration_en
    assert "diagnosed again" in migration_en


def test_release_playbook_documents_exact_tag_commit_gate():
    playbook = read_doc("docs/wiki/release-playbook.md")

    assert "refs/tags/" in playbook
    assert "annotated tag peel" in playbook
    assert "reusable" in playbook
    assert "exact commit" in playbook
    assert "package job" in playbook
    assert "full verification" in playbook
    assert "只创建并推送 tag" in playbook
    assert "绝不由 release gate 推送 main" in playbook


def test_v412_release_docs_define_gateway_restart_race_contract():
    changelog = read_doc("CHANGELOG.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    notes = read_doc("docs/release-notes-v4.1.2.md")
    notes_en = read_doc("docs/release-notes-v4.1.2.en.md")
    migration = read_doc("docs/migration.md")
    migration_en = read_doc("docs/migration.en.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    controls = read_doc("docs/wiki/v4.1-safety-controls.md")
    event_flow = read_doc("docs/wiki/event-flow.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")
    workflow = read_doc(".github/workflows/tests.yml")
    todo = read_doc("TODO.md")

    assert "## V4.1.2" in changelog
    assert "docs/release-notes-v4.1.2.md" in changelog
    assert "docs/release-notes-v4.1.2.md" in readme
    assert "docs/release-notes-v4.1.2.en.md" in readme_en
    assert "HFC_VERSION: v4.4.1" in workflow
    assert "### V4.1.2：Gateway 重启竞态热修（已发布）" in todo
    assert "当前发布候选为 `4.4.1`" in readiness
    assert "Current release candidate: `4.4.1`" in readiness_en

    for text in (notes, notes_en, controls, event_flow, acceptance):
        assert "runtime_heartbeat_stale" in text or "heartbeat stale" in text
        assert "runtime.hello" in text
        assert "fence" in text
    assert "从 V4.1.1 升级到 V4.1.2" in migration
    assert "Upgrading From V4.1.1 To V4.1.2" in migration_en
    assert "不测试或改变自动压缩行为" in notes
    assert "does not test or change automatic compression behavior" in notes_en

    public_docs = "\n".join(
        (
            notes,
            notes_en,
            migration,
            migration_en,
            readiness,
            readiness_en,
            controls,
            event_flow,
            acceptance,
        )
    )
    assert "/private/tmp" not in public_docs
    assert "/Users/" not in public_docs
    assert re.search(r"\b(?:oc|om|ou)_[0-9a-f]{16,}\b", public_docs) is None
    assert re.search(r"FEISHU_APP_SECRET=(?!xxx\b)[^\s]+", public_docs) is None


def test_v414_release_docs_define_manifestless_legacy_migration_candidate():
    changelog = read_doc("CHANGELOG.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    notes = read_doc("docs/release-notes-v4.1.4.md")
    notes_en = read_doc("docs/release-notes-v4.1.4.en.md")
    migration = read_doc("docs/migration.md")
    migration_en = read_doc("docs/migration.en.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")
    workflow = read_doc(".github/workflows/tests.yml")
    compose = read_doc("docker-compose.example.yml")
    todo = read_doc("TODO.md")

    assert "## V4.1.4" in changelog
    assert "docs/release-notes-v4.1.4.md" in changelog
    assert "docs/release-notes-v4.1.4.md" in readme
    assert "docs/release-notes-v4.1.4.en.md" in readme_en
    assert "HFC_VERSION=v4.4.1" in install_doc
    assert "HFC_VERSION: v4.4.1" in workflow
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.4.1}"' in compose
    assert "### V4.1.4：Windows 旧版 manifest 迁移热修（已发布）" in todo
    assert "当前发布候选为 `4.4.1`" in readiness
    assert "Current release candidate: `4.4.1`" in readiness_en
    assert "## V4.1.4 发布门禁" in readiness
    assert "## V4.1.4 Release Gates" in readiness_en
    assert "从 V4.1.3 升级到 V4.1.4" in migration
    assert "Upgrading From V4.1.3 To V4.1.4" in migration_en
    assert "V4.1.4 Windows 旧版安装迁移验收" in acceptance

    public_docs = "\n".join(
        (
            notes,
            notes_en,
            migration,
            migration_en,
            readiness,
            readiness_en,
            acceptance,
        )
    )
    for marker in (
        "Issue #171",
        "manifest: rebuilt",
        "fail-closed",
        "CRLF",
        "Windows",
    ):
        assert marker in public_docs
    assert "v4.0.14 的官方 CLI 会创建 manifest" in notes
    assert "public v4.0.14 CLI does create a manifest" in notes_en
    assert "/private/tmp" not in public_docs
    assert "/Users/" not in public_docs
    assert re.search(r"\b(?:oc|om|ou)_[0-9a-f]{16,}\b", public_docs) is None
    assert re.search(r"FEISHU_APP_SECRET=(?!xxx\b)[^\s]+", public_docs) is None


def test_v413_release_docs_define_combined_upgrade_compatibility_candidate():
    changelog = read_doc("CHANGELOG.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    notes = read_doc("docs/release-notes-v4.1.3.md")
    notes_en = read_doc("docs/release-notes-v4.1.3.en.md")
    migration = read_doc("docs/migration.md")
    migration_en = read_doc("docs/migration.en.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")
    workflow = read_doc(".github/workflows/tests.yml")
    todo = read_doc("TODO.md")

    assert "## V4.1.3" in changelog
    assert "docs/release-notes-v4.1.3.md" in changelog
    assert "docs/release-notes-v4.1.3.md" in readme
    assert "docs/release-notes-v4.1.3.en.md" in readme_en
    assert "HFC_VERSION=v4.4.1" in install_doc
    assert "HFC_VERSION: v4.4.1" in workflow
    assert "### V4.1.3：升级恢复与 TurnRunner 兼容性热修（已发布）" in todo
    assert "当前发布候选为 `4.4.1`" in readiness
    assert "Current release candidate: `4.4.1`" in readiness_en
    assert "## V4.1.3 发布门禁" in readiness
    assert "## V4.1.3 Release Gates" in readiness_en

    for text in (notes, notes_en, migration, migration_en, readiness, readiness_en):
        assert "target" in text
        assert "CAS" in text
        assert "acknowledge-review" in text
        assert "migrate-safe" in text
        assert "fail-closed" in text or "fails closed" in text
    assert "从 V4.1.2 升级到 V4.1.3" in migration
    assert "Upgrading From V4.1.2 To V4.1.3" in migration_en
    assert "V4.1.3 升级恢复" in acceptance
    assert "不得手工修改 fence" in acceptance

    combined_candidate_docs = "\n".join(
        (changelog, readme, readme_en, notes, notes_en, migration, migration_en, readiness, readiness_en, acceptance, todo)
    )
    for marker in ("Issue #158", "PR #168", "Issue #169", "TurnRunner"):
        assert marker in combined_candidate_docs
    for text in (notes, notes_en, migration, migration_en, readiness, readiness_en, acceptance):
        assert "1a3a9de" in text

    public_docs = "\n".join(
        (notes, notes_en, migration, migration_en, readiness, readiness_en, acceptance)
    )
    assert "/private/tmp" not in public_docs
    assert "/Users/" not in public_docs
    assert re.search(r"\b(?:oc|om|ou)_[0-9a-f]{16,}\b", public_docs) is None
    assert re.search(r"FEISHU_APP_SECRET=(?!xxx\b)[^\s]+", public_docs) is None


def test_v410_release_docs_define_ack_multibot_and_compose_boundaries():
    notes = read_doc("docs/release-notes-v4.1.0.md")
    notes_en = read_doc("docs/release-notes-v4.1.0.en.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    protocol = read_doc("docs/event-protocol.md")
    protocol_en = read_doc("docs/event-protocol.en.md")
    controls = read_doc("docs/wiki/v4.1-safety-controls.md")

    for text in (notes, notes_en, protocol, protocol_en):
        for marker in (
            "POST /native-handoff/recover",
            "POST /native-handoff/ack",
            "hfc-native-handoff-recovery-v2",
            "hfc-native-handoff-ack-v1",
            "uncertain",
        ):
            assert marker in text

    assert "不承诺永久 exactly-once" in notes
    assert "does not promise forever exactly-once" in notes_en
    assert "obligation/content/plan/route/target" in notes
    assert "obligation/content/plan/route/target" in notes_en
    assert "schema v4" in notes
    assert "schema v4" in notes_en
    assert "target_hash" in controls

    for text in (notes, notes_en, guide, guide_en, controls):
        assert "#162" in text
        assert "im:message.group_at_msg.include_bot:readonly" in text
        assert "native_chats" in text

    for text in (notes, notes_en, controls):
        for marker in (
            "install-docker.sh",
            "non-root",
            "runtime.hello",
            "POST /events",
        ):
            assert marker in text


def test_v410_docs_close_release_ux_review_drift():
    changelog = read_doc("CHANGELOG.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    testing = read_doc("docs/testing.md")
    testing_en = read_doc("docs/testing.en.md")
    architecture = read_doc("docs/architecture.md")
    architecture_en = read_doc("docs/architecture.en.md")
    notes = read_doc("docs/release-notes-v4.1.0.md")
    notes_en = read_doc("docs/release-notes-v4.1.0.en.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    controls = read_doc("docs/wiki/v4.1-safety-controls.md")
    event_flow = read_doc("docs/wiki/event-flow.md")

    assert "setup container runs `install-docker.sh` as root" in changelog
    assert "sidecar, Gateway, and probe run as non-root" in changelog

    assert "一小时幂等窗口" in controls
    assert "窗口外" in controls
    assert "人工复核" in controls
    assert "保证" not in controls.split("## integrity 模式与升级迁移", 1)[0]
    assert "durable record 可读" in event_flow
    assert "状态丢失或损坏" in event_flow
    assert "优先避免丢失答案" in event_flow
    assert "不承诺永久 exactly-once" in event_flow

    for text in (readme, testing):
        assert "Hermes 0.19.0" in text
        assert "v2026.7.20" in text
        assert "自动化 strategy detection" in text
        assert "本机真实源码" in text
    assert "`0.18.x` / `0.19.0` / `v2026.5.16+`" in testing
    for text in (readme_en, testing_en, install_doc):
        assert "Hermes 0.19.0" in text
        assert "v2026.7.20" in text
        assert "automated strategy detection" in text.lower()
        assert "real local source" in text
    assert "`0.18.x` / `0.19.0` / `v2026.5.16+`" in testing_en
    assert "`v2026.7.20`, `0.19.0`" in install_doc

    for text in (architecture, architecture_en):
        assert "`POST /native-handoff/recover`" in text
        assert "`POST /native-handoff/ack`" in text

    for text in (notes, notes_en, guide, guide_en, controls):
        assert "im:message.group_at_msg.include_bot:readonly" in text
        assert "im.message.receive_v1" in text
    for text in (notes, guide, controls):
        assert "发布新版本" in text
    for text in (notes_en, guide_en):
        assert "publish a new app version" in text

    assert "Existing-container Docker smoke for V3.9.0" not in install_doc
    assert "V4.1.0 automated Compose gate" in install_doc
    assert "real Docker" in install_doc
    assert "real Feishu" in install_doc
    assert "pending acceptance" in install_doc


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


def test_all_command_feedback_card_lifecycle_is_documented():
    event_flow = read_doc("docs/wiki/event-flow.md")
    maintenance = read_doc("docs/wiki/maintenance-guide.md")
    acceptance = read_doc("docs/wiki/feishu-acceptance.md")
    todo = read_doc("TODO.md")

    for phrase in (
        "all slash command feedback",
        "same card",
        "fail-open",
        "/compress",
        "/model",
        "/resume",
        "/update",
    ):
        assert phrase in event_flow
    assert "固定 command allowlist" in maintenance
    assert "create/PATCH 成功才抑制" in maintenance
    assert "V4.0.13 全命令反馈卡片" in acceptance
    assert "手动 `/compress`" in acceptance
    assert "V4.0.13：Hermes 全命令反馈卡片化" in todo

    wiki = read_doc("docs/wiki/README.md")
    assert "全命令反馈卡片" in wiki
    assert "plugin/quick" in wiki

    readiness = read_doc("docs/release-readiness.md")
    assert "所有非空文本反馈" in readiness
    assert "重启前反馈进入命令卡" in readiness


def test_v420_docs_define_private_update_maintenance_release():
    changelog = read_doc("CHANGELOG.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    notes = read_doc("docs/release-notes-v4.2.0.md")
    notes_en = read_doc("docs/release-notes-v4.2.0.en.md")
    event_flow = read_doc("docs/wiki/event-flow.md")
    maintenance_guide = read_doc("docs/wiki/maintenance-guide.md")
    todo = read_doc("TODO.md")
    workflow = read_doc(".github/workflows/tests.yml")

    assert "## V4.2.0" in changelog
    assert "docs/release-notes-v4.2.0.md" in changelog
    assert "docs/release-notes-v4.2.0.md" in readme
    assert "docs/release-notes-v4.2.0.en.md" in readme_en
    assert "From V4.2.0" in install_doc
    assert "## V4.2.0 飞书私聊安全升级" in guide
    assert "## V4.2.0 Safe Private-Chat Updates" in guide_en
    assert "当前发布候选为 `4.4.1`" in readiness
    assert "Current release candidate: `4.4.1`" in readiness_en
    assert "## V4.2.0 发布门禁" in readiness
    assert "## V4.2.0 Release Gates" in readiness_en
    assert "### V4.2.0：飞书私聊安全升级（已发布）" in todo
    assert "HFC_VERSION: v4.4.1" in workflow

    for text in (install_doc, guide, guide_en, readiness, readiness_en, notes, notes_en):
        assert "maintenance status" in text
        assert "/update" in text

    assert "仅拦截飞书私聊中的裸 `/update`" in notes
    assert "Only an exact bare `/update` in a Feishu private chat" in notes_en
    assert "群聊、非飞书、别名和带参数调用仍进入 Hermes 原处理器" in notes
    assert "Group, non-Feishu, alias, and parameterized commands" in notes_en
    assert "连续两次新 heartbeat" in notes
    assert "two newer heartbeats" in notes_en
    assert "执行时重新 fetch 最新 `origin/main`" in notes
    assert "fetch the latest `origin/main` at execution time" in notes_en
    assert "单次 `_active_work_count()` 采样" in notes
    assert "one `_active_work_count()` sample" in notes_en
    assert "一次性私有凭据快照" in notes
    assert "one-use private credential snapshot" in notes_en
    assert "完整依赖" in notes
    assert "full wheel dependencies" in notes_en
    assert "schema v2 heartbeat" in event_flow
    assert "drain lease" in event_flow
    assert "schema v2" in maintenance_guide

    assets = (
        "hermes-feishu-card-v4.2.0-macos.tar.gz",
        "hermes-feishu-card-v4.2.0-linux.tar.gz",
        "hermes-feishu-card-v4.2.0-windows.zip",
        "hermes-feishu-card-v4.2.0-checksums.txt",
    )
    for text in (notes, notes_en):
        for asset in assets:
            assert asset in text

    assert "候选版本说明；本实现分支不会自行发布" not in notes
    assert "Candidate release notes. This implementation branch" not in notes_en


def test_v428_docs_cover_credential_persistence_release_contracts():
    changelog = read_doc("CHANGELOG.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    notes = read_doc("docs/release-notes-v4.2.8.md")
    notes_en = read_doc("docs/release-notes-v4.2.8.en.md")
    todo = read_doc("TODO.md")

    for text in (changelog, readme, readme_en, install_doc, guide, guide_en, todo):
        assert "V4.2.8" in text or "v4.2.8" in text
    assert "当前发布候选为 `4.4.1`" in readiness
    assert "Current release candidate: `4.4.1`" in readiness_en
    assert "## V4.2.8 发布门禁" in readiness
    assert "## V4.2.8 Release Gates" in readiness_en

    for text in (notes, notes_en):
        for marker in (
            "install.sh",
            "install-docker.sh",
            "install.ps1",
            "FEISHU_APP_ID",
            "FEISHU_APP_SECRET",
            ".env",
            "0600",
            "PowerShell",
            "site-packages",
        ):
            assert marker in text
    assert "持久化" in notes
    assert "persistence" in notes_en

    assets = (
        "hermes-feishu-card-v4.2.8-macos.tar.gz",
        "hermes-feishu-card-v4.2.8-linux.tar.gz",
        "hermes-feishu-card-v4.2.8-windows.zip",
        "hermes-feishu-card-v4.2.8-checksums.txt",
    )
    for text in (notes, notes_en):
        for asset in assets:
            assert asset in text


def test_v429_docs_cover_interaction_and_quote_release_contracts():
    changelog = read_doc("CHANGELOG.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    notes = read_doc("docs/release-notes-v4.2.9.md")
    notes_en = read_doc("docs/release-notes-v4.2.9.en.md")
    maintenance = read_doc("docs/wiki/maintenance-guide.md")
    event_flow = read_doc("docs/wiki/event-flow.md")
    todo = read_doc("TODO.md")

    for text in (changelog, readme, readme_en, install_doc, guide, guide_en, todo):
        assert "V4.2.9" in text or "v4.2.9" in text
    assert "当前发布候选为 `4.4.1`" in readiness
    assert "Current release candidate: `4.4.1`" in readiness_en
    assert "## V4.2.9 发布门禁" in readiness
    assert "## V4.2.9 Release Gates" in readiness_en

    for text in (notes, notes_en):
        for marker in (
            "Issue #197",
            "PR #196",
            "PR #199",
            "callback token",
            "site-packages",
            "/events",
        ):
            assert marker in text
    assert "回答摘录" in notes
    assert "answer excerpt" in notes_en
    assert "interaction_id" in notes_en
    assert "callback token" in maintenance
    assert "config.summary" in event_flow

    assets = (
        "hermes-feishu-card-v4.2.9-macos.tar.gz",
        "hermes-feishu-card-v4.2.9-linux.tar.gz",
        "hermes-feishu-card-v4.2.9-windows.zip",
        "hermes-feishu-card-v4.2.9-checksums.txt",
    )
    for text in (notes, notes_en):
        for asset in assets:
            assert asset in text


def test_v421_docs_define_first_gateway_heartbeat_hotfix():
    changelog = read_doc("CHANGELOG.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    notes = read_doc("docs/release-notes-v4.2.1.md")
    notes_en = read_doc("docs/release-notes-v4.2.1.en.md")
    maintenance_guide = read_doc("docs/wiki/maintenance-guide.md")
    todo = read_doc("TODO.md")

    assert "## V4.2.1" in changelog
    assert "docs/release-notes-v4.2.1.md" in changelog
    assert "docs/release-notes-v4.2.1.md" in readme
    assert "docs/release-notes-v4.2.1.en.md" in readme_en
    assert "V4.2.1 registers the live Gateway runner" in install_doc
    assert "第一条私聊裸 `/update`" in guide
    assert "first bare private-chat `/update`" in guide_en
    assert "当前发布候选为 `4.4.1`" in readiness
    assert "Current release candidate: `4.4.1`" in readiness_en
    assert "## V4.2.1 发布门禁" in readiness
    assert "## V4.2.1 Release Gates" in readiness_en
    assert "### V4.2.1：Gateway 首个 heartbeat 任务计数热修（已发布）" in todo
    assert "V4.2.1 要求 startup adapter" in maintenance_guide

    for text in (notes, notes_en):
        assert "_active_work_count()" in text
        assert "active_work_count_complete=true" in text

    assets = (
        "hermes-feishu-card-v4.2.1-macos.tar.gz",
        "hermes-feishu-card-v4.2.1-linux.tar.gz",
        "hermes-feishu-card-v4.2.1-windows.zip",
        "hermes-feishu-card-v4.2.1-checksums.txt",
    )
    for text in (notes, notes_en):
        for asset in assets:
            assert asset in text


def test_v422_docs_define_async_update_transition_publish():
    changelog = read_doc("CHANGELOG.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    notes = read_doc("docs/release-notes-v4.2.2.md")
    notes_en = read_doc("docs/release-notes-v4.2.2.en.md")
    maintenance_guide = read_doc("docs/wiki/maintenance-guide.md")
    todo = read_doc("TODO.md")
    workflow = read_doc(".github/workflows/tests.yml")

    assert "## V4.2.2" in changelog
    assert "docs/release-notes-v4.2.2.md" in changelog
    assert "docs/release-notes-v4.2.2.md" in readme
    assert "docs/release-notes-v4.2.2.en.md" in readme_en
    assert "V4.2.2 keeps the native card-action callback fast" in install_doc
    assert "取消进入“已取消更新”终态" in guide
    assert "cancel reaches a terminal state" in guide_en
    assert "当前发布候选为 `4.4.1`" in readiness
    assert "Current release candidate: `4.4.1`" in readiness_en
    assert "## V4.2.2 发布门禁" in readiness
    assert "## V4.2.2 Release Gates" in readiness_en
    assert "### V4.2.2：更新确认卡终态写回热修（已发布）" in todo
    assert "V4.2.2 要求 native action 快速 ACK" in maintenance_guide
    assert "HFC_VERSION: v4.4.1" in workflow

    for text in (notes, notes_en):
        for marker in (
            "operation_id",
            "PATCH",
            "cancelled",
            "locking",
            "378 passed",
            "2307 passed",
        ):
            assert marker in text

    assets = (
        "hermes-feishu-card-v4.2.2-macos.tar.gz",
        "hermes-feishu-card-v4.2.2-linux.tar.gz",
        "hermes-feishu-card-v4.2.2-windows.zip",
        "hermes-feishu-card-v4.2.2-checksums.txt",
    )
    for text in (notes, notes_en):
        for asset in assets:
            assert asset in text


def test_v423_docs_define_update_evidence_forwarding_hotfix():
    changelog = read_doc("CHANGELOG.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    notes = read_doc("docs/release-notes-v4.2.3.md")
    notes_en = read_doc("docs/release-notes-v4.2.3.en.md")
    maintenance_guide = read_doc("docs/wiki/maintenance-guide.md")
    todo = read_doc("TODO.md")
    workflow = read_doc(".github/workflows/tests.yml")
    compose = read_doc("docker-compose.example.yml")

    assert "## V4.2.3" in changelog
    assert "docs/release-notes-v4.2.3.md" in changelog
    assert "docs/release-notes-v4.2.3.md" in readme
    assert "docs/release-notes-v4.2.3.en.md" in readme_en
    assert "V4.2.3 forwards the update evidence fingerprint" in install_doc
    assert "更新证据指纹" in guide
    assert "update evidence fingerprint" in guide_en
    assert "当前发布候选为 `4.4.1`" in readiness
    assert "Current release candidate: `4.4.1`" in readiness_en
    assert "## V4.2.3 发布门禁" in readiness
    assert "## V4.2.3 Release Gates" in readiness_en
    assert "### V4.2.3：更新回调证据转发热修（已发布）" in todo
    assert "V4.2.3 要求 WebSocket hook" in maintenance_guide
    assert "HFC_VERSION: v4.4.1" in workflow
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.4.1}"' in compose

    for text in (notes, notes_en):
        for marker in (
            "update_evidence_fingerprint",
            "WebSocket",
            "sidecar",
            "fail-closed",
        ):
            assert marker in text

    assets = (
        "hermes-feishu-card-v4.2.3-macos.tar.gz",
        "hermes-feishu-card-v4.2.3-linux.tar.gz",
        "hermes-feishu-card-v4.2.3-windows.zip",
        "hermes-feishu-card-v4.2.3-checksums.txt",
    )
    for text in (notes, notes_en):
        for asset in assets:
            assert asset in text


def test_v424_docs_define_quoted_reply_card_isolation_hotfix():
    changelog = read_doc("CHANGELOG.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    notes = read_doc("docs/release-notes-v4.2.4.md")
    notes_en = read_doc("docs/release-notes-v4.2.4.en.md")
    maintenance_guide = read_doc("docs/wiki/maintenance-guide.md")
    todo = read_doc("TODO.md")
    workflow = read_doc(".github/workflows/tests.yml")
    compose = read_doc("docker-compose.example.yml")

    assert "## V4.2.4" in changelog
    assert "docs/release-notes-v4.2.4.md" in changelog
    assert "docs/release-notes-v4.2.4.md" in readme
    assert "docs/release-notes-v4.2.4.en.md" in readme_en
    assert "V4.2.4 gives every new Feishu/Lark topic reply" in install_doc
    assert "真实入站 message ID" in guide
    assert "real incoming message ID" in guide_en
    assert "当前发布候选为 `4.4.1`" in readiness
    assert "Current release candidate: `4.4.1`" in readiness_en
    assert "## V4.2.4 发布门禁" in readiness
    assert "## V4.2.4 Release Gates" in readiness_en
    assert "### V4.2.4：话题引用回复独立卡片热修（发布候选）" in todo
    assert "V4.2.4 要求 `message.started`" in maintenance_guide
    assert "HFC_VERSION: v4.4.1" in workflow
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.4.1}"' in compose

    for text in (notes, notes_en):
        for marker in (
            "message.started",
            "message_id",
            "reply alias",
            "PR #177",
            "Issue #175",
        ):
            assert marker in text

    assets = (
        "hermes-feishu-card-v4.2.4-macos.tar.gz",
        "hermes-feishu-card-v4.2.4-linux.tar.gz",
        "hermes-feishu-card-v4.2.4-windows.zip",
        "hermes-feishu-card-v4.2.4-checksums.txt",
    )
    for text in (notes, notes_en):
        for asset in assets:
            assert asset in text


def test_v425_docs_map_every_audit_fix_and_release_gate():
    changelog = read_doc("CHANGELOG.md")
    readme = read_doc("README.md")
    readme_en = read_doc("README.en.md")
    install_doc = read_doc("README-install.md")
    guide = read_doc("docs/user-guide.md")
    guide_en = read_doc("docs/user-guide.en.md")
    readiness = read_doc("docs/release-readiness.md")
    readiness_en = read_doc("docs/release-readiness.en.md")
    notes = read_doc("docs/release-notes-v4.2.5.md")
    notes_en = read_doc("docs/release-notes-v4.2.5.en.md")
    todo = read_doc("TODO.md")
    workflow = read_doc(".github/workflows/tests.yml")
    compose = read_doc("docker-compose.example.yml")

    assert "## V4.2.5" in changelog
    assert "docs/release-notes-v4.2.5.md" in changelog
    assert "docs/release-notes-v4.2.5.md" in readme
    assert "docs/release-notes-v4.2.5.en.md" in readme_en
    assert "V4.2.5 hardens quoted-turn identity" in install_doc
    assert "canonical `turn_id`" in guide
    assert "canonical `turn_id`" in guide_en
    assert "当前发布候选为 `4.4.1`" in readiness
    assert "Current release candidate: `4.4.1`" in readiness_en
    assert "## V4.2.5 发布门禁" in readiness
    assert "## V4.2.5 Release Gates" in readiness_en
    assert "### V4.2.5：审查安全热修（已发布）" in todo
    assert "HFC_VERSION: v4.4.1" in workflow
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.4.1}"' in compose

    audit_ids = [f"HFC-REV-20260801-{index:02d}" for index in range(1, 10)]
    for text in (notes, notes_en):
        for audit_id in audit_ids:
            assert audit_id in text
        for marker in (
            "turn_id",
            "alias fallback",
            "non-terminal delta",
            "delivery policy",
            "external drain",
            "acknowledge-review",
            "config.yaml.example",
        ):
            assert marker in text
    assert "重复 maintenance resume" in notes
    assert "已确认的 Hermes checkout" in notes
    assert "稳定 `vX.Y.Z` tag" in notes
    assert "精确 commit" in notes
    assert "已注解" in notes
    assert "duplicate maintenance resume" in notes_en
    assert "confirmed Hermes checkout" in notes_en
    assert "stable `vX.Y.Z` tag" in notes_en
    assert "exact commit" in notes_en
    assert "annotated" in notes_en

    assets = (
        "hermes-feishu-card-v4.2.5-macos.tar.gz",
        "hermes-feishu-card-v4.2.5-linux.tar.gz",
        "hermes-feishu-card-v4.2.5-windows.zip",
        "hermes-feishu-card-v4.2.5-checksums.txt",
    )
    for text in (notes, notes_en):
        for asset in assets:
            assert asset in text
