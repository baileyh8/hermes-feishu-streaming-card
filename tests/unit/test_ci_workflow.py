from __future__ import annotations

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_official_image_gate_covers_real_install_and_bytecode_states():
    workflow = yaml.safe_load((ROOT / ".github/workflows/tests.yml").read_text())
    job = workflow["jobs"]["official-image-acceptance"]
    assert job["needs"] == "plan"
    assert job["if"] == "needs.plan.outputs.mode == 'full'"
    assert job["runs-on"] == "ubuntu-latest"
    cases = job["strategy"]["matrix"]["include"]
    identities = {(case["image"], case["pyc_mode"]) for case in cases}
    assert len(identities) == len(cases)
    assert {image for image, _ in identities} == {
        "v0.22.1", "v0.22.2", "v0.22.3", "v0.22.4", "v0.22.5"
    }
    assert ("v0.22.5", "preserved") in identities
    assert ("v0.22.5", "cleared") in identities
    assert any(case["legacy"] for case in cases)
    assert "matrix.pyc_mode" in job["name"]
    windows = workflow["jobs"]["pytest-windows"]
    assert "tests/unit/test_launch_source_paths.py" in next(
        step["run"] for step in windows["steps"] if step.get("name") == "Test"
    )
    command = next(step["run"] for step in job["steps"]
                   if step.get("name") == "Run pinned official image acceptance")
    assert "tools/official_image_acceptance.py" in command
    assert "--diagnose" not in command
    assert "--legacy-wheel" in command
    upload = next(step for step in job["steps"] if "upload-artifact@" in step.get("uses", ""))
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "error"


def test_github_actions_runs_full_pytest_matrix():
    workflow = ROOT / ".github" / "workflows" / "tests.yml"

    text = workflow.read_text(encoding="utf-8")

    assert "pull_request:" in text
    assert "push:" in text
    full_job = yaml.safe_load(text)["jobs"]["pytest"]
    assert full_job["strategy"]["matrix"]["python-version"] == [
        "3.9", "3.10", "3.11", "3.12", "3.13", "3.14"
    ]
    assert 'python -m pip install -e ".[test]"' in text
    assert "python -m pytest -q" in text
    assert "powershell-installer:" in text
    assert "runs-on: windows-latest" in text
    assert "ParseFile" in text
    assert "install.ps1" in text
    assert "docker-compose-runtime-smoke:" in text
    assert "docker compose -f docker-compose.example.yml config --quiet" in text
    assert "docker compose -f docker-compose.smoke.yml up" in text
    assert "--detach --wait --wait-timeout 180" in text
    assert "docker compose -f docker-compose.smoke.yml run" in text
    assert "--rm --no-deps probe" in text
    assert "docker compose -f docker-compose.smoke.yml logs" in text
    assert "--no-color setup sidecar gateway" in text
    assert "--abort-on-container-exit" not in text
    assert "pytest-windows:" in text
    assert "pytest-macos:" in text
    assert "runs-on: macos-latest" in text
    jobs = yaml.safe_load(text)["jobs"]
    assert "python -m tools.ci_run" in str(jobs["pytest-shards"])
    assert "python -m tools.ci_run" in str(jobs["macos-shards"])
    assert "tools.ci_shard" in str(jobs["pytest"])
    assert "tools.ci_shard" in str(jobs["pytest-macos"])


def test_windows_gate_runs_fixed_portable_runtime_core_without_exclusions():
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8"
        )
    )
    windows_job = workflow["jobs"]["pytest-windows"]
    test_step = next(
        step for step in windows_job["steps"] if step.get("name") == "Test"
    )
    command = test_step["run"]
    required_suites = {
        "tests/unit/test_event_auth.py",
        "tests/unit/test_hook_runtime.py",
        "tests/unit/test_lifecycle.py",
        "tests/unit/test_render.py",
        "tests/unit/test_runner.py",
        "tests/unit/test_session.py",
        "tests/integration/test_card_freeze.py",
        "tests/integration/test_clarify_multi_select.py",
        "tests/integration/test_feishu_client_http.py",
        "tests/integration/test_hook_runtime_integration.py",
        "tests/integration/test_server.py",
    }

    assert required_suites <= set(command.split())
    assert "--ignore" not in command
    assert "-k" not in command
    assert windows_job.get("continue-on-error") is not True


def test_pr_workflows_do_not_duplicate_codex_branch_push_runs():
    for name in ("tests.yml", "codeql.yml"):
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        )
        triggers = workflow.get("on", workflow.get(True))

        assert triggers["push"]["branches"] == ["main"]
        assert workflow["concurrency"] == {
            "group": "${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}",
            "cancel-in-progress": True,
        }


def test_release_assets_workflow_supports_manual_package_dry_run():
    workflow = ROOT / ".github" / "workflows" / "release-assets.yml"

    text = workflow.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "inputs:" in text
    assert "tag_ref:" in text
    assert "Build install packages" in text
    assert "gh release upload" in text
    assert "python scripts/release_archives.py build" in text
    assert "python scripts/release_archives.py validate" in text


def test_release_workflow_packages_only_after_reusable_test_gate():
    text = (ROOT / ".github" / "workflows" / "release-assets.yml").read_text(
        encoding="utf-8"
    )

    assert "resolve-release:" in text
    assert "release-gate:" in text
    assert "uses: ./.github/workflows/tests.yml" in text
    assert "needs: [resolve-release, release-gate]" in text
    assert "checkout_ref: ${{ needs.resolve-release.outputs.commit }}" in text


def test_release_write_permission_is_scoped_to_package_job():
    text = (ROOT / ".github" / "workflows" / "release-assets.yml").read_text(
        encoding="utf-8"
    )
    prefix, package = text.split("  package:", 1)

    assert "permissions:\n  contents: read" in prefix
    assert "contents: write" not in prefix
    assert "permissions:\n      contents: write" in package


def test_reusable_ci_checks_out_requested_ref_in_every_job():
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8"
        )
    )
    checkout_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]

    assert checkout_steps
    import json

    sources = json.loads((ROOT / "tests/fixtures/hermes_upstream_sources.json").read_text())
    upstream_checkouts = {}
    for step in checkout_steps:
        inputs = step.get("with", {})
        if "repository" not in inputs:
            assert inputs.get("ref") == "${{ inputs.checkout_ref || github.sha }}"
        else:
            assert inputs["repository"] == "NousResearch/hermes-agent"
            upstream_checkouts[inputs.get("path")] = inputs.get("ref")
    assert upstream_checkouts == {
        **{
            f".upstream/{baseline}": evidence["commit"]
            for baseline, evidence in sources.items()
        },
        ".upstream/issue471_9b904": "9b904dcc22c23fe8b82834339bbdd96495072e59",
    }
    steps = workflow["jobs"]["upstream-shards"]["steps"]
    source_step = next(step for step in steps if "test_upstream_hermes_compat.py" in step.get("run", ""))
    for baseline in sources:
        assert source_step["env"][f"HFC_UPSTREAM_{baseline.upper()}_ROOT"] == (
            "${{ github.workspace }}/.upstream/" + baseline
        )
    before_source = "\n".join(step.get("run", "") for step in steps[:steps.index(source_step)])
    assert "ruamel.yaml" in before_source
    pm_step = next(step for step in steps if "test_pm_generation.py" in step.get("run", ""))
    assert pm_step["env"]["HFC_UPSTREAM_PM_ROOT"] == "${{ github.workspace }}/.upstream/issue428_7bda2a"
    assert pm_step["env"]["HFC_REQUIRE_PM_GENERATION"] == "1"
    assert source_step["env"]["HFC_LEGACY_PLUGIN_SOURCE_ROOT"] == "${{ github.workspace }}/.upstream/baseline_0222"
    for family, baseline in {
        "PM_MAIN": "baseline_main_0ab", "CF4": "issue451_cf4", "RC2": "issue451_rc2",
        "0224": "latest_stable", "0225": "issue456_0225", "MAIN_4E58": "latest_main",
    }.items():
        assert source_step["env"][f"HFC_CONFIG_WRITER_{family}_ROOT"] == (
            "${{ github.workspace }}/.upstream/" + baseline
        )
    from tools.ci_run import FORWARD
    assert "HFC_CONFIG_WRITER_0225_ROOT" in FORWARD
    assert sources["issue456_0225"]["commit"] == "bf0203a794a07aac3ff764972ab9087baa4e4f4e"
    assert sources["latest_stable"]["commit"] == "4d04303818b61bf3aa09278af48356315515965b"
    assert sources["latest_main"]["commit"] == "4e58c721c8cf9b51c46636ea84ea6a5cb540880a"
    assert sources["baseline_0222"]["commit"] == "d923a5eb487a17c0a68801bba9ef0f56c81040a8"
    assert sources["baseline_main_0ab"]["commit"] == "0ab43d4f67e41be75f4f6c0fa14a86dbcfd3e5b3"
    preparation = "\n".join(step.get("run", "") for step in steps[:steps.index(pm_step)])
    assert 'HFC_TEST_WHEEL=' in preparation
    assert "pip wheel --no-deps --wheel-dir" in preparation


def test_official_actions_are_sha_pinned_to_node24_capable_releases():
    expected = {
        "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/setup-python": "6d4fd22b20465ccc0f7354476588e8df7c9ca0e0",
        "actions/upload-artifact": "cf430e030ddbb5b0abf93d22962f4752f3646cd9",
        "actions/download-artifact": "9000827ccba6bdab643e8b6fd33ac0654aef8333",
        "github/codeql-action/init": "9b3254085494b55a0f7c4c2b3d2d52cc712ce370",
        "github/codeql-action/analyze": "9b3254085494b55a0f7c4c2b3d2d52cc712ce370",
    }
    workflow_paths = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

    found = set()
    for workflow_path in workflow_paths:
        text = workflow_path.read_text(encoding="utf-8")
        for action, ref in re.findall(
            r"uses:\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+)@([^\s#]+)",
            text,
        ):
            if action.startswith(("actions/", "github/codeql-action/")):
                assert ref == expected[action]
                assert re.fullmatch(r"[0-9a-f]{40}", ref)
                found.add(action)

    assert found == set(expected)


def test_codeql_scans_python_on_push_pull_request_and_schedule():
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "codeql.yml").read_text(
            encoding="utf-8"
        )
    )

    assert workflow["permissions"] == {
        "contents": "read",
        "security-events": "write",
    }
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"push", "pull_request", "schedule"}
    analyze = workflow["jobs"]["analyze"]
    assert analyze["runs-on"] == "ubuntu-latest"
    assert analyze["strategy"]["matrix"]["language"] == ["python"]
    uses = [step.get("uses", "") for step in analyze["steps"]]
    assert any(value.startswith("github/codeql-action/init@") for value in uses)
    assert any(value.startswith("github/codeql-action/analyze@") for value in uses)


def test_dependabot_updates_pip_and_github_actions_weekly():
    config = yaml.safe_load(
        (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    )

    assert config["version"] == 2
    updates = config["updates"]
    assert {item["package-ecosystem"] for item in updates} == {
        "pip",
        "github-actions",
    }
    assert all(item["directory"] == "/" for item in updates)
    assert all(item["schedule"]["interval"] == "weekly" for item in updates)


def test_reusable_ci_runs_powershell_installer_tests():
    text = (ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8"
    )

    assert "Test Windows installer contract" in text
    assert (
        'python -m pytest tests/unit/test_install_scripts.py -k "powershell" -q'
        in text
    )
    assert "gh release upload" not in text
    assert "gh release create" not in text


def test_release_package_reruns_full_gate_after_exact_checkout():
    text = (ROOT / ".github" / "workflows" / "release-assets.yml").read_text(
        encoding="utf-8"
    )
    package = text.split("  package:", 1)[1]

    checkout_index = package.index(
        "ref: ${{ needs.resolve-release.outputs.commit }}"
    )
    verifier_positions = []
    start = 0
    needle = "python scripts/verify_release.py"
    while True:
        position = package.find(needle, start)
        if position < 0:
            break
        verifier_positions.append(position)
        start = position + len(needle)
    build_index = package.index("Build install packages")
    upload_index = min(
        package.index("gh release upload"),
        package.index("gh release create"),
    )

    assert len(verifier_positions) == 2
    assert checkout_index < verifier_positions[0] < build_index
    assert build_index < verifier_positions[1] < upload_index
    assert package.count("--require-main-ancestor") == 2
    assert '--title "$RELEASE_TAG" --generate-notes --draft' in package


def test_release_dispatch_accepts_only_full_semver_tag_ref():
    text = (ROOT / ".github" / "workflows" / "release-assets.yml").read_text(
        encoding="utf-8"
    )

    assert "tag_ref:" in text
    assert "^refs/tags/v[0-9]+\\.[0-9]+\\.[0-9]+$" in text
    assert 'tag="${tag_ref#refs/tags/}"' in text


def test_docker_compose_example_documents_container_paths():
    compose = (ROOT / "docker-compose.example.yml").read_text(encoding="utf-8")

    assert "image: your-hermes-image:latest" in compose
    assert "/opt/hermes" in compose
    assert "/opt/data" in compose
    assert "FEISHU_APP_ID" in compose
    assert "FEISHU_APP_SECRET" in compose
    assert "install-docker.sh" in compose


def test_docker_compose_runtime_smoke_uses_published_install_topology():
    source = (ROOT / "docker-compose.smoke.yml").read_text(encoding="utf-8")
    compose = yaml.safe_load(source)
    services = compose["services"]

    assert set(services) == {"setup", "sidecar", "gateway", "probe"}
    setup = services["setup"]
    sidecar = services["sidecar"]
    gateway = services["gateway"]
    probe = services["probe"]

    assert setup["user"] == "0:0"
    for service in (sidecar, gateway, probe):
        assert service["user"] == "65532:65532"
        assert service.get("privileged") is not True

    setup_command = "\n".join(setup["command"])
    assert "/src/install-docker.sh" in setup_command
    assert "tests/fixtures/hermes_v2026_4_23" in setup_command
    assert setup["environment"]["HFC_INSTALL_SOURCE"] == "/tmp/hfc-install-source"
    assert "mkdir -p /tmp/hfc-install-source" in setup_command
    assert "cp /src/pyproject.toml /src/README.md /src/LICENSE" in setup_command
    assert "cp -R /src/hermes_feishu_card" in setup_command
    assert ".:/src:ro" in setup["volumes"]
    assert setup["environment"]["HFC_TEST_NOOP_DELIVERY"] == "1"
    assert setup["environment"]["HFC_SKIP_START"] == "1"
    assert sidecar["depends_on"]["setup"]["condition"] == "service_completed_successfully"
    assert gateway["depends_on"]["sidecar"]["condition"] == "service_healthy"
    assert probe["depends_on"]["gateway"]["condition"] == "service_healthy"

    for service in services.values():
        volumes = service["volumes"]
        assert any("hermes-runtime:/opt/hermes" in volume for volume in volumes)
        assert any("hermes-data:/opt/data" in volume for volume in volumes)
        assert any("hfc-smoke-state:/opt/hfc-state" in volume for volume in volumes)

    assert "privileged:" not in source
    assert "systemd" not in source.lower()


def test_docker_compose_runtime_smoke_executes_patched_gateway_hook():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.smoke.yml").read_text(encoding="utf-8")
    )
    config = yaml.safe_load(
        (ROOT / "tests" / "fixtures" / "docker-smoke-config.yaml").read_text(
            encoding="utf-8"
        )
    )
    fixture = (
        ROOT / "tests" / "fixtures" / "docker_gateway_smoke.py"
    ).read_text(encoding="utf-8")
    gateway = compose["services"]["gateway"]
    probe = compose["services"]["probe"]
    probe_command = "\n".join(probe["command"])

    assert "docker_gateway_smoke.py" in "\n".join(gateway["command"])
    assert "healthcheck" in gateway
    assert "HERMES_FEISHU_CARD_PATCH_BEGIN" in fixture
    assert "_handle_message_with_agent" in fixture
    assert "gateway-result.json" in fixture
    assert 'health["metrics"]["runtime_control_events_received"] >= 1' in probe_command
    assert 'health["metrics"]["runtime_control_events_accepted"] >= 1' in probe_command
    assert 'health["metrics"]["events_received"] >= 1' in probe_command
    assert 'health["metrics"]["event_auth_rejections"] == 0' in probe_command
    assert 'health["readiness"]["status"] == "degraded"' in probe_command
    assert 'health["readiness"]["reason"] == "runtime_owner_unverified"' in probe_command
    assert "--stay-alive" in "\n".join(gateway["command"])
    assert 'receipt["patched_events_before_direct"] >= 1' in probe_command
    assert 'receipt["event_response"]["disposition"] == "native"' in probe_command
    assert "sign_event_request" not in probe_command
    assert config["feishu"] == {"app_id": "", "app_secret": ""}
    assert config["integrity"] == {"mode": "safe"}


def test_archive_safety_gate_preserves_real_platform_and_root_coverage():
    jobs = yaml.safe_load((ROOT / '.github/workflows/tests.yml').read_text())['jobs']
    job = jobs['release-archive-safety']
    assert job['timeout-minutes'] == 25
    assert job['strategy']['matrix']['os'] == ['ubuntu-latest', 'macos-latest']
    step = next(step for step in job['steps'] if step.get('name') == 'Verify actual archive construction and extraction')
    assert step['env']['HFC_ARCHIVE_ROOT_TEST'] == "${{ runner.os == 'Linux' && '1' || '0' }}"
    assert 'tests/unit/test_release_archives.py' in step['run']
    assert 'HFC_ARCHIVE_EVIDENCE_DIR' in step['run']
    assert '--junitxml' in step['run']
    workflow = (ROOT / '.github/workflows/release-assets.yml').read_text()
    assert workflow.index('release_archives.py build') < workflow.index('release_archives.py validate') < workflow.index('gh release upload')
