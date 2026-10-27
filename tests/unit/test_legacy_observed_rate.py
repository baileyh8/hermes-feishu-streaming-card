import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
from types import SimpleNamespace

import pytest
import yaml

from hermes_feishu_card import hook_runtime, hermes_plugin_runtime as runtime_module
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.render import render_card
from hermes_feishu_card.session import CardSession
from hermes_feishu_card.observed_rate import ObservedRates
from hermes_feishu_card.install import legacy_observer as install, plugin
from hermes_feishu_card.install import plugin_config_bytes
from hermes_feishu_card.install.plugin_config_bytes import ConfigBytesPlan


class _ExactPlan(ConfigBytesPlan):
    def verify_source(self) -> None:
        pass


def _exact_plan(postimage: bytes) -> ConfigBytesPlan:
    return _ExactPlan(postimage, Path("/verified"), "fixed")


def _copy_fixed_support(root: Path) -> None:
    configured = os.environ.get("HFC_FIXED_TAG_SOURCE_ROOT")
    if not configured:
        pytest.skip("HFC_FIXED_TAG_SOURCE_ROOT required for launch guard")
    source = Path(configured)
    support = {"hermes_cli/main.py", "hermes_cli/__init__.py"}
    support.update(plugin_config_bytes.FAMILIES["fixed"])
    for relative in support:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source / relative).read_bytes())


def observation(**changes):
    return dict(session_id="synthetic-session", turn_id="synthetic-turn", platform="feishu",
                api_request_id="synthetic-request", model="synthetic-model", provider="synthetic-provider",
                usage={"input_tokens": 10, "output_tokens": 80}, api_duration=2.0, **changes)


@pytest.fixture
def runtime(monkeypatch):
    posted = []
    runtime = runtime_module.PluginRuntime(post=lambda *a: posted.append(a), observe_only=True)
    monkeypatch.setattr(hook_runtime, "_plugin_runtime", lambda: runtime)
    yield runtime, posted
    runtime.close()


def complete(runtime, **changes):
    values = {"response": "A synthetic answer long enough for measured tokens. " * 8,
              "agent_result": {"api_calls": 1, "session_id": "synthetic-session", "output_tokens": 80}}
    values.update(changes)
    return hook_runtime._event_data("message.completed", values, None, None)


def test_legacy_completion_renders_rate_with_no_plugin_events(runtime):
    rt, posted = runtime
    values = observation()
    rt.handle_pre_llm_call(**values)
    rt.handle_post_api_request(**values)
    rt.handle_post_api_request(**values)  # duplicate counted once
    for name in ("handle_post_llm_call", "handle_on_session_end", "handle_pre_tool_call",
                 "handle_post_tool_call", "handle_pre_approval_request", "handle_subagent_start"):
        getattr(rt, name)(**values)
    data = complete(rt)
    assert data["rate"] == {"output_tps": 40.0, "basis": "request"}
    session = CardSession(conversation_id="synthetic-c", message_id="synthetic-m", chat_id="synthetic-chat")
    assert session.apply(SidecarEvent.from_dict({
        "schema_version": "1", "event": "message.completed", "conversation_id": "synthetic-c",
        "message_id": "synthetic-m", "chat_id": "synthetic-chat", "platform": "feishu",
        "sequence": 1, "created_at": 1.0, "data": data,
    }))
    assert "~40 tok/s" in json.dumps(render_card(session, footer_fields=["output_rate"]))
    assert "rate" not in complete(rt)
    # A duplicate pre/post pair after consumption cannot revive the observation.
    rt.handle_pre_llm_call(**values)
    rt.handle_post_api_request(**values)
    assert "rate" not in complete(rt)
    assert posted == []
    assert rt.runtime_activity_snapshot() == (0, True)
    assert not rt.start_runtime_interaction_listener(b"s" * 32)


@pytest.mark.parametrize("case", ["session", "model", "provider", "absent", "broken"])
def test_unknown_rate_is_hidden_without_blocking_completion(runtime, monkeypatch, case):
    rt, _ = runtime
    values = observation()
    rt.handle_pre_llm_call(**values)
    rt.handle_post_api_request(**values)
    changes = {}
    if case in ("model", "provider"):
        rt.handle_post_api_request(**{**values, "api_request_id": "second", case: "different"})
        changes["agent_result"] = {"api_calls": 1, "session_id": "synthetic-session", "output_tokens": 160}
    elif case in ("estimate", "clipped", "mismatch", "session"):
        changes["agent_result"] = {"api_calls": 1, "session_id": "other" if case == "session" else "synthetic-session",
                                   "output_tokens": {"estimate": 0, "clipped": 99999, "mismatch": 81, "session": 80}[case]}
    elif case == "absent":
        monkeypatch.setattr(hook_runtime, "_plugin_runtime", lambda: None)
    elif case == "broken":
        monkeypatch.setattr(rt, "take_observed_rate", lambda *_: 1 / 0)
    data = complete(rt, **changes)
    assert data["answer"] and "rate" not in data


def test_ttl_capacity_ambiguity_and_late_observations():
    clock = [0.0]
    cache = ObservedRates(now=lambda: clock[0], capacity=2, ttl=10)
    first = observation()
    cache.start(first)
    cache.observe(first)
    second = {**first, "session_id": "second"}
    third = {**first, "session_id": "overflow"}
    for values in (second, third):
        cache.start(values)
        cache.observe(values)
    assert cache.take("overflow") is None
    assert cache.take("second")[1] == 80
    clock[0] = 10
    cache.observe(first)
    assert cache.take("synthetic-session") is None
    cache.start(first)
    cache.observe(first)
    overlap = {**first, "turn_id": "overlap"}
    cache.start(overlap)
    cache.observe(overlap)
    assert cache.take("synthetic-session") is None


def test_capacity_rejection_cannot_borrow_same_session_previous_rate():
    cache = ObservedRates(capacity=1)
    values = observation()
    cache.start(values)
    cache.observe(values)
    cache.start({**values, "turn_id": "new-turn"})
    assert cache.take("synthetic-session") is None


def test_footer_estimate_does_not_disqualify_exact_observations(runtime):
    rt, _ = runtime
    rt.handle_pre_llm_call(**observation())
    rt.handle_post_api_request(**observation())
    data = complete(rt, response="x" * 320, agent_result={"api_calls": 1, "session_id": "synthetic-session"})
    assert data["tokens"]["output_tokens"] == 80
    assert data["rate"]["output_tps"] == 40.0


@pytest.fixture
def installed(tmp_path, monkeypatch):
    root, home = tmp_path / "checkout", tmp_path / "home"
    root.mkdir()
    home.mkdir()
    _copy_fixed_support(root)
    original = b"# retain comments and CRLF\r\nplugins: {}\r\nother: value\r\n"
    (home / "config.yaml").write_bytes(original)
    binding = plugin.HermesRuntimeBinding(root, Path(sys.executable), Path(sys.executable),
        "sha256:" + "a" * 64, home, home / "config.yaml", root, root)
    monkeypatch.setattr(plugin, "resolve_runtime_binding", lambda **_: binding)
    monkeypatch.setattr(plugin, "_plan_enabled_config_bytes", lambda b, p, raw, desired, enabled:
        _exact_plan(yaml.safe_dump(desired).encode()))
    monkeypatch.setattr(plugin, "probe_plugin_entrypoint", lambda _: SimpleNamespace(status="verified"))
    calls = []
    def enable(_):
        calls.append("enable")
        config, _ = plugin._expected_enabled_config(yaml.safe_load(binding.config_path.read_bytes()))
        binding.config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        return 0
    monkeypatch.setattr(plugin, "_run_official_enable", enable)
    return binding, original, calls


@pytest.mark.parametrize("command", ["restore", "uninstall", "rollback"])
def test_config_install_reinstall_and_exact_restore(installed, command):
    binding, original, calls = installed
    registration = install.prepare(binding)
    assert registration is not None
    marker = (binding.checkout_root / install.MARKER).read_bytes()
    assert install.prepare(binding) is None
    assert calls == ["enable"]
    assert (binding.checkout_root / install.MARKER).read_bytes() == marker
    registration.finish()
    if command == "rollback":
        registration.rollback()
    else:
        install.restore(binding.checkout_root)
    assert binding.config_path.read_bytes() == original
    assert not (binding.checkout_root / install.MARKER).exists()
    assert list((binding.hermes_home / ".hermes_feishu_card" / "install").iterdir()) == []


def test_marker_failure_rolls_back_config(installed, monkeypatch):
    binding, original, _ = installed
    write = plugin._write_private_new
    def fail(path, data):
        if path.name == install.MARKER:
            raise OSError("synthetic marker failure")
        return write(path, data)
    monkeypatch.setattr(plugin, "_write_private_new", fail)
    with pytest.raises(OSError, match="synthetic marker"):
        install.prepare(binding)
    assert binding.config_path.read_bytes() == original


def test_config_drift_refuses_restore(installed):
    binding, _, _ = installed
    install.prepare(binding)
    binding.config_path.write_bytes(b"other: changed\n")
    with pytest.raises(ValueError, match="changed"):
        install.restore(binding.checkout_root)
    assert binding.config_path.read_bytes() == b"other: changed\n"


@pytest.mark.parametrize("command", ["restore", "uninstall", "failure", "late_failure"])
def test_cli_source_and_plugin_transaction(installed, monkeypatch, command):
    from hermes_feishu_card import cli
    from hermes_feishu_card.install import recovery
    from hermes_feishu_card.install.detect import detect_hermes
    binding, original, calls = installed
    root = binding.checkout_root
    fixture = Path(__file__).parents[1] / "fixtures" / "hermes_decomposed"
    shutil.copytree(fixture, root, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    (root / "VERSION").write_text("0.22.2\n")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*.py")}
    monkeypatch.setattr(cli, "resolve_runtime_binding", lambda **_: binding)
    monkeypatch.setattr(cli, "_ensure_hermes_runtime_package", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_ensure_hermes_feishu_sdk", lambda *a, **k: None)
    # Eligibility source contract has its own upstream test; exercise the actual
    # source transaction here using the repository's hermetic decomposed fixture.
    monkeypatch.setattr(install, "available", lambda _: True)
    if command == "late_failure":
        from hermes_feishu_card.install import file_transaction
        write = file_transaction._atomic_write_text_portable
        failed = []
        def late_fail(path, *a, **k):
            if path.name == ".hermes_feishu_card_manifest" and not failed:
                failed.append(True)
                raise OSError("synthetic late source failure")
            return write(path, *a, **k)
        monkeypatch.setattr(recovery, "_secure_dirfd_transactions_supported", lambda: False)
        monkeypatch.setattr(file_transaction, "_atomic_write_text_portable", late_fail)
        assert cli.main(["install", "--hermes-dir", str(root), "--yes"]) == 1
        assert failed
    elif command == "failure":
        def fail(*a, **k):
            raise ValueError("synthetic source install failure")
        monkeypatch.setattr(recovery, "install_decomposed", fail)
        assert cli.main(["install", "--hermes-dir", str(root), "--yes"]) == 1
    else:
        assert cli.main(["install", "--hermes-dir", str(root), "--yes"]) == 0
        assert cli.main(["install", "--hermes-dir", str(root), "--yes"]) == 0
        assert calls == ["enable"]
        assert cli._diagnose_install_state(detect_hermes(root))["status"] == "installed"
        assert cli._repair_install_state(detect_hermes(root)) == []
        result = cli.main([command, "--hermes-dir", str(root), "--yes"])
        if os.name == "nt":
            # Existing recovery gate deliberately requires POSIX dirfd. Preserve
            # its refusal, restored config and retry evidence on Windows.
            assert result == 1
            assert binding.config_path.read_bytes() == original
            assert (root / install.MARKER).exists()
            return
        assert result == 0
    assert binding.config_path.read_bytes() == original
    assert before == {p: (root / p).read_bytes() for p in before}
    assert not (root / install.MARKER).exists()


def test_enable_journal_failure_restores_preimage(installed, monkeypatch):
    binding, original, _ = installed
    monkeypatch.setattr(plugin, "_rewrite_private_journal", lambda *a, **k: (_ for _ in ()).throw(OSError("journal")))
    with pytest.raises(OSError, match="journal"):
        install.prepare(binding)
    assert binding.config_path.read_bytes() == original


def test_existing_v4_install_gains_observer_without_replacing_source_preimages(installed, monkeypatch, capsys):
    from hermes_feishu_card import cli
    from hermes_feishu_card.install import recovery
    from hermes_feishu_card.install.detect import detect_hermes
    binding, _, calls = installed
    root = binding.checkout_root
    shutil.copytree(Path(__file__).parents[1] / "fixtures" / "hermes_decomposed", root,
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    (root / "VERSION").write_text("0.22.2\n")
    recovery.install_decomposed(detect_hermes(root))
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    monkeypatch.setattr(cli, "resolve_runtime_binding", lambda **_: binding)
    monkeypatch.setattr(cli, "_ensure_hermes_runtime_package", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_ensure_hermes_feishu_sdk", lambda *a, **k: None)
    monkeypatch.setattr(install, "available", lambda _: True)
    assert cli.main(["install", "--hermes-dir", str(root), "--yes"]) == 0
    assert "gateway.restart_required" in capsys.readouterr().out
    assert calls == ["enable"]
    assert before == {p: (root / p).read_bytes() for p in before}


@pytest.mark.parametrize("case", ["home", "unknown_version", "uninspectable"])
def test_invalid_marker_cannot_admit_observer(installed, monkeypatch, case):
    binding, _, _ = installed
    install.prepare(binding)
    from hermes_feishu_card import instance_binding
    monkeypatch.setattr(instance_binding, "gateway_process_binding", lambda: (
        binding.checkout_root, binding.hermes_home / "other" if case == "home" else binding.hermes_home))
    if case == "unknown_version":
        marker = binding.checkout_root / install.MARKER
        value = json.loads(marker.read_bytes())
        value["protocol"] = "unknown-version"
        marker.write_text(json.dumps(value))
    if case == "uninspectable":
        monkeypatch.setattr(install, "_read", lambda _: (_ for _ in ()).throw(PermissionError("synthetic")))
    assert install.process_observer_mode() == "disabled"


@pytest.mark.parametrize("isolation", [None, "host"])
def test_real_marker_bootstrap_never_starts_control_or_listener(installed, monkeypatch, isolation):
    binding, _, _ = installed
    if isolation:
        binding.config_path.write_text("plugins:\n  isolation: host\n", encoding="utf-8")
    install.prepare(binding)
    (binding.checkout_root / ".hermes_feishu_card_manifest").write_text('{"manifest_version":2}')
    from hermes_feishu_card import instance_binding
    monkeypatch.setattr(instance_binding, "gateway_process_binding", lambda: (binding.checkout_root, binding.hermes_home))
    def forbidden(*a, **k):
        pytest.fail("observe-only must not start runtime ownership or transport")
    monkeypatch.setattr(runtime_module, "acquire_runtime_control", forbidden)
    monkeypatch.setattr(runtime_module, "read_transport_root_secret", forbidden)
    hooks = {}
    ctx = SimpleNamespace(register_hook=lambda name, fn: hooks.update({name: fn}))
    runtime_module.reset_production_plugin_runtime_for_tests()
    try:
        runtime_module.bootstrap_plugin_runtime(ctx)
        assert set(hooks) == set(runtime_module.OFFICIAL_HOOKS)
        rt = runtime_module.active_plugin_runtime()
        if isolation:
            assert rt is None
        else:
            assert rt.observe_only
            hooks["pre_llm_call"](**observation())
            hooks["post_api_request"](**observation())
            assert rt.take_observed_rate("synthetic-session")[1] == 80
    finally:
        runtime_module.reset_production_plugin_runtime_for_tests()


def test_real_v0222_plugin_manager_load(tmp_path):
    configured = os.environ.get("HFC_LEGACY_PLUGIN_SOURCE_ROOT")
    if not configured:
        pytest.skip("set HFC_LEGACY_PLUGIN_SOURCE_ROOT to the read-only Hermes v0.22.2 checkout")
    root = Path(configured).resolve()
    assert root.is_dir()
    commit = subprocess.check_output(
        ["git", "-c", "safe.directory=" + root.as_posix(), "rev-parse", "HEAD"],
        cwd=root, text=True, timeout=10,
    ).strip()
    assert commit.startswith("d923a5eb48"), "fixture must be the pinned Hermes v0.22.2 commit"
    assert install.available(root)
    code = r'''
import sys, os
from pathlib import Path
sys.path[:0] = [sys.argv[2], sys.argv[1]]
from hermes_cli.plugins import PluginManager
from hermes_feishu_card import hermes_plugin_runtime as runtime
from hermes_feishu_card.install import legacy_observer
# The loader/entrypoint/registration are real. The install fact is exercised
# separately above, without mutating this upstream checkout.
legacy_observer.process_observer_mode = lambda: "observe"
runtime.acquire_runtime_control = lambda **kw: (_ for _ in ()).throw(AssertionError("hello"))
manager = PluginManager(scope_key=os.environ["HERMES_HOME"])
manifests = [m for m in manager._scan_entry_points() if m.name == "hermes-feishu-card"]
assert len(manifests) == 1
manager._load_plugin(manifests[0])
assert manager._plugins["hermes-feishu-card"].enabled
assert set(manager._hooks) == set(runtime.OFFICIAL_HOOKS)
values = dict(session_id="synthetic-session", turn_id="synthetic-turn", platform="feishu",
              api_request_id="synthetic-api", model="model", provider="provider",
              usage={"input_tokens": 10, "output_tokens": 80}, api_duration=2.0)
for name in ("pre_llm_call", "post_api_request"):
    for callback in manager._hooks[name]:
        callback(**values)
assert runtime.active_plugin_runtime().take_observed_rate("synthetic-session")[1] == 80
runtime.reset_production_plugin_runtime_for_tests()
'''
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("plugins:\n  enabled: [hermes-feishu-card]\n  isolation: in_process\n")
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "HERMES_HOME": str(home),
           "PYTHONDONTWRITEBYTECODE": "1"}
    child = subprocess.run([sys.executable, "-c", code, str(Path.cwd()), str(root)],
                           env=env, capture_output=True, text=True, timeout=60)
    assert child.returncode == 0, child.stdout + child.stderr


@pytest.mark.parametrize("legacy_counts", [(300, 700), (0, 99999)])
def test_cached_agent_consecutive_turns_use_own_observations(runtime, legacy_counts):
    rt, _ = runtime
    for index, legacy_count in enumerate(legacy_counts):
        values = {**observation(), "turn_id": f"turn-{index}",
                  "usage": {"input_tokens": 10, "output_tokens": 80 + index * 40}}
        rt.handle_pre_llm_call(**values)
        rt.handle_post_api_request(**values)
        result = {"api_calls": 1, "session_id": "synthetic-session", "output_tokens": legacy_count}
        data = complete(rt, response="x" * 1000, agent_result=result)
        expected = hook_runtime._completion_tokens({"agent_result": result}, "x" * 1000)
        assert data["tokens"] == expected  # Footer semantics are unchanged.
        assert data["rate"]["output_tps"] == 40 + index * 20
        session = CardSession(conversation_id="c", message_id="m", chat_id="chat")
        session.apply(SidecarEvent.from_dict({
            "schema_version": "1", "event": "message.completed", "conversation_id": "c",
            "message_id": "m", "chat_id": "chat", "platform": "feishu",
            "sequence": 1, "created_at": 1.0, "data": data}))
        assert f"~{40 + index * 20} tok/s" in json.dumps(render_card(session, footer_fields=["output_rate"]))


def test_consumed_turns_do_not_exhaust_capacity():
    cache = ObservedRates()
    for index in range(600):
        values = {**observation(), "turn_id": f"turn-{index}"}
        cache.start(values)
        cache.observe(values)
        assert cache.take("synthetic-session")[0]["output_tps"] == 40
        assert len(cache.entries) <= 256


@pytest.mark.parametrize("change", [{"usage": None}, {"usage": {"input_tokens": 10}},
    {"moa_references": ["reference"]}, {"api_duration": -1}, {"usage": {"output_tokens": 19}}])
def test_invalid_observations_remain_hidden(runtime, change):
    rt, _ = runtime
    values = {**observation(), **change}
    rt.handle_pre_llm_call(**values)
    rt.handle_post_api_request(**values)
    assert "rate" not in complete(rt)


@pytest.mark.parametrize("failure", ["root", "home"])
def test_unlocatable_observer_preserves_hybrid_bootstrap(tmp_path, monkeypatch, failure):
    from hermes_feishu_card import instance_binding
    root = tmp_path / "checkout"
    source = root / "hermes_cli" / "plugins.py"
    source.parent.mkdir(parents=True)
    if failure != "root":
        source.write_text("")
    (root / ".hermes_feishu_card_manifest").write_text('{"manifest_version":3}')
    monkeypatch.setattr(instance_binding, "gateway_process_binding", lambda: None)
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", SimpleNamespace(__file__=str(source)))
    def unavailable():
        raise OSError("synthetic HOME failure")
    monkeypatch.setitem(sys.modules, "hermes_constants", SimpleNamespace(get_process_hermes_home=unavailable))
    assert install.process_observer_mode() == "hybrid"
    monkeypatch.setattr(runtime_module, "read_transport_root_secret", lambda: b"s" * 32)
    monkeypatch.setattr(runtime_module.PluginRuntime, "start_runtime_interaction_listener", lambda *a: True)
    monkeypatch.setattr(runtime_module, "acquire_runtime_control", lambda **k: SimpleNamespace(close=lambda: None))
    ctx = SimpleNamespace(register_hook=lambda *a: None)
    runtime_module.reset_production_plugin_runtime_for_tests()
    try:
        runtime_module.bootstrap_plugin_runtime(ctx)
        rt = runtime_module.active_plugin_runtime()
        assert rt is not None and not rt.observe_only
    finally:
        runtime_module.reset_production_plugin_runtime_for_tests()


def test_observer_diagnostics_are_read_only(installed):
    binding, _, _ = installed
    root = binding.checkout_root
    assert install.observer_status(root)["status"] == "not_installed"
    install.prepare(binding)
    manifest = root / ".hermes_feishu_card_manifest"
    manifest.write_text('{"manifest_version":2}')
    for expected in ("active", "disabled"):
        if expected == "disabled":
            manifest.write_text('{"manifest_version":3}')
        before = {p: p.read_bytes() for p in root.parent.rglob("*") if p.is_file()}
        status = install.observer_status(root, home=binding.hermes_home)
        assert status["status"] == expected and status["reason"]
        assert before == {p: p.read_bytes() for p in before}


@pytest.mark.parametrize("consumer", ["legacy", "v3"])
@pytest.mark.parametrize("failure", ["official", "journal", "none"])
def test_shared_plugin_transaction_portable_rollback(installed, monkeypatch, consumer, failure):
    binding, original, _ = installed
    # Both installers use this same public config transaction boundary.
    if consumer == "v3":
        (binding.checkout_root / ".hermes_feishu_card_manifest").write_text('{"manifest_version":3}')
    # Restrict the missing-fchmod simulation to the plugin's own portable
    # journal helper. Deleting it from the shared os module also breaks the
    # host's POSIX dirfd backend, which still requires the real operation.
    class OSWithoutFchmod:
        def __getattr__(self, name):
            if name == "fchmod":
                raise AttributeError(name)
            return getattr(os, name)
    monkeypatch.setattr(plugin, "os", OSWithoutFchmod())
    if failure == "official":
        def fail(_):
            binding.config_path.write_bytes(b"plugins: {}\n")
            raise OSError("official failure")
        monkeypatch.setattr(plugin, "_run_official_enable", fail)
    elif failure == "journal":
        monkeypatch.setattr(plugin, "_rewrite_private_journal", lambda *a, **k: (_ for _ in ()).throw(OSError("journal")))
    if consumer == "legacy":
        execute = lambda: install.prepare(binding)
    else:
        preimage = plugin.prepare_plugin_config(binding)
        execute = lambda: plugin.enable_plugin(binding, preimage)
    if failure != "none":
        with pytest.raises((OSError, plugin.PluginConfigRefused)):
            execute()
    else:
        result = execute()
        if consumer == "legacy":
            result.rollback()
        else:
            plugin.restore_plugin_config(binding, preimage, result)
            # V3 returns the journal to prepared after rollback. Exercise its
            # actual path-chmod fallback without changing the host backend.
            chmod_calls = []
            original_chmod = Path.chmod
            def record_chmod(path, mode, *args, **kwargs):
                chmod_calls.append((path, mode))
                return original_chmod(path, mode, *args, **kwargs)
            with monkeypatch.context() as patch:
                patch.setattr(Path, "chmod", record_chmod)
                plugin.mark_plugin_config_prepared(preimage)
            assert len(chmod_calls) == 1
            assert chmod_calls[0][0].parent == preimage.state_dir
            assert chmod_calls[0][1] == 0o600
            assert json.loads(preimage.journal_path.read_bytes()) == {
                "protocol": "hfc-plugin-config-preimage-v1",
                "phase": "prepared",
                **preimage.sanitized(),
            }
    if failure == "official":
        # Unknown official postimage can contain concurrent user edits. Retain
        # those bytes and the original private preimage for diagnosis/retry.
        assert binding.config_path.read_bytes() == b"plugins: {}\n"
        state = binding.hermes_home / ".hermes_feishu_card" / "install"
        assert list(state.glob("hfc-config-preimage-*.yaml"))
        assert list(state.glob("hfc-config-preimage-*.json"))
    else:
        assert binding.config_path.read_bytes() == original


def completion_locals(**changes):
    return {"platform": "feishu", "chat_id": "synthetic-chat",
            "conversation_id": "synthetic-conversation", "message_id": "synthetic-message",
            "created_at": 1777017600.0, "response": "Full answer MEDIA:/tmp/synthetic.png",
            "agent_result": {"api_calls": 1, "session_id": "synthetic-session", "output_tokens": 80}, **changes}


@pytest.mark.parametrize("internal", [False, True])
def test_completion_preview_preserves_rate_for_actual_event(runtime, internal):
    rt, posted = runtime
    rt.handle_pre_llm_call(**observation())
    rt.handle_post_api_request(**observation())
    values = completion_locals()
    if internal:
        values["source"] = SimpleNamespace(_hfc_internal_turn_id="synthetic-queued-turn")
    first = hook_runtime.build_event("message.completed", values, preview=True)
    second = hook_runtime.build_event("message.completed", values, preview=True)
    actual = hook_runtime.build_event("message.completed", values)
    assert actual["data"]["rate"] == {"output_tps": 40.0, "basis": "request"}
    assert first["sequence"] == second["sequence"] == actual["sequence"]
    for field in ("answer", "attachments", "native_delivery", "tokens"):
        assert first["data"][field] == second["data"][field] == actual["data"][field]
    assert actual["data"]["answer"] == "Full answer"
    assert actual["data"]["attachments"]
    assert actual["data"]["native_delivery"] == "required"
    rt.handle_post_api_request(**observation())  # Late duplicate cannot revive it.
    assert "rate" not in hook_runtime.build_event("message.completed", values)["data"]
    assert posted == []


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError(5, "I/O"),
                                   NotADirectoryError("parent")])
def test_uninspectable_marker_metadata_cannot_enter_hybrid(tmp_path, monkeypatch, error):
    from hermes_feishu_card import instance_binding
    root, home = tmp_path / "root", tmp_path / "home"
    root.mkdir()
    home.mkdir()
    original_lstat = Path.lstat
    def denied(path, *args, **kwargs):
        if path == root / install.MARKER:
            raise error
        return original_lstat(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", denied)
    monkeypatch.setattr(instance_binding, "gateway_process_binding", lambda: (root, home))
    assert install.observer_status(root)["status"] == "disabled"
    assert install.process_observer_mode() == "disabled"
    monkeypatch.setattr(runtime_module, "acquire_runtime_control", lambda **k: pytest.fail("runtime owner"))
    monkeypatch.setattr(runtime_module, "read_transport_root_secret", lambda: pytest.fail("transport"))
    runtime_module.reset_production_plugin_runtime_for_tests()
    try:
        runtime_module.bootstrap_plugin_runtime(SimpleNamespace(register_hook=lambda *a: None))
        assert runtime_module.active_plugin_runtime() is None
    finally:
        runtime_module.reset_production_plugin_runtime_for_tests()


# Source-derived from the pinned Hermes v0.22.2 observation helper/call.
_OBSERVER_SOURCE_CONTRACT = """def _fire_post_api_request_hook(
    agent: Any, response: Any, assistant_message: Any, finish_reason: Any, *, api_messages: Any,
    api_call_count: Any, api_duration: Any, api_start_time: Any, api_request_id: Any,
    effective_task_id: Any, turn_id: Any,
) -> None:
    from agent.conversation_loop import _moa_reference_metrics_for_hook

    try:
        from hermes_cli.lifecycle import has_hook, invoke_hook as _invoke_hook
        if has_hook("post_api_request"):
            _invoke_hook(
                "post_api_request",
                task_id=effective_task_id,
                turn_id=turn_id,
                api_request_id=api_request_id,
                session_id=agent.session_id or "",
                platform=agent.platform or "",
                model=agent.model,
                provider=agent.provider,
                base_url=agent.base_url,
                api_mode=agent.api_mode,
                api_call_count=api_call_count,
                api_duration=api_duration,
                started_at=api_start_time,
                ended_at=api_start_time + api_duration,
                # First stream chunk time (epoch s); None if not streamed / no chunk.
                # TTFB = first_chunk_at - started_at.
                first_chunk_at=getattr(agent, "_last_api_first_chunk_at", None),
                finish_reason=finish_reason,
                message_count=len(api_messages),
                response_model=getattr(response, "model", None),
                response=agent._api_response_payload_for_hook(
                    response, assistant_message, finish_reason=finish_reason
                ),
                usage=agent._usage_summary_for_api_request_hook(response),
                context_length=getattr(getattr(agent, "context_compressor", None), "context_length", None),
                assistant_message=assistant_message,
                assistant_content_chars=len(assistant_message.content or ""),
                assistant_tool_call_count=len(getattr(assistant_message, "tool_calls", None) or []),
                moa_references=_moa_reference_metrics_for_hook(agent),
            )
    except Exception:
        pass

def normalize_model_response():
    splice_provider_projection(agent, response, messages)
    _fire_post_api_request_hook(
            agent, response, assistant_message, finish_reason, api_messages=api_messages,
            api_call_count=api_call_count, api_duration=api_duration, api_start_time=api_start_time,
            api_request_id=api_request_id, effective_task_id=effective_task_id, turn_id=turn_id,
        )
"""


@pytest.mark.parametrize("change", [None, "usage", "duration", "request", "session",
                                   "caller", "nested", "position", "syntax"])
def test_observer_admission_binds_values_and_response_intake(tmp_path, change):
    source = _OBSERVER_SOURCE_CONTRACT
    if change == "usage":
        source = source.replace("agent._usage_summary_for_api_request_hook(response)", "agent.session_usage")
    elif change == "duration":
        source = source.replace("ended_at=api_start_time + api_duration", "ended_at=api_start_time")
    elif change == "request":
        source = source.replace("api_request_id=api_request_id", "api_request_id=turn_id")
    elif change == "session":
        source = source.replace("session_id=agent.session_id", "session_id=agent.other_session_id")
    elif change == "caller":
        source = source.replace("api_duration=api_duration, api_start_time=api_start_time", "api_duration=0, api_start_time=api_start_time")
    elif change == "nested":
        source = source.replace("    _fire_post_api_request_hook(\n", "    if False:\n        _fire_post_api_request_hook(\n")
    elif change == "position":
        source = source.replace("    splice_provider_projection(agent, response, messages)\n", "")
    elif change == "syntax":
        source = "invalid source: ("
    path = tmp_path / "agent" / "turn_response_intake.py"
    path.parent.mkdir()
    path.write_text(source)
    assert install.available(tmp_path) is (change is None)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["emit", "queued", "stage"])
async def test_actual_completion_entrypoints_freeze_rate_and_retry_payload(runtime, monkeypatch, mode):
    import asyncio
    rt, _ = runtime
    rt.handle_pre_llm_call(**observation())
    rt.handle_post_api_request(**observation())
    values = completion_locals()
    if mode == "queued":
        values["source"] = SimpleNamespace(_hfc_internal_turn_id="synthetic-queued-final")
    config = hook_runtime.RuntimeConfig(
        enabled=True, event_url="http://127.0.0.1:12345/events", timeout_seconds=0.2,
        delta_coalesce_ms=0, delta_coalesce_chars=0, delta_coalesce_max_pending=10)
    monkeypatch.setattr(hook_runtime, "load_runtime_config", lambda: config)
    monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda *_: None)
    async def allow(*args, **kwargs):
        return hook_runtime._PolicyGateResult(True, None)
    async def flush(*args):
        pass
    monkeypatch.setattr(hook_runtime, "_policy_gate_async", allow)
    monkeypatch.setattr(hook_runtime, "_flush_pending_deltas_for_local_vars", flush)
    monkeypatch.setattr(hook_runtime, "TERMINAL_DELIVERY_RETRY_DELAYS", (0,))
    posted = []
    async def post(url, payload, timeout):
        posted.append(payload)
        if len(posted) == 1 and mode != "stage":
            # A late observation during a transport retry must not change payload.
            rt.handle_post_api_request(**observation())
            raise TimeoutError("synthetic lost response")
        return {"ok": True, "applied": True}
    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", post)
    preview = hook_runtime.build_event("message.completed", values, preview=True)
    if mode == "stage":
        assert await hook_runtime.stage_message_completed_from_hermes_locals_async(values)
        assert posted == []
        stage = hook_runtime._exact_completion_stage_for_current_task()
        assert stage["task_id"] == id(asyncio.current_task())
        payload = stage["payload"]
        assert payload["data"]["rate"] == {"output_tps": 40.0, "basis": "request"}
        returned = await hook_runtime.prepare_exact_base_final_delivery({
            "delivery_adapter": SimpleNamespace(name="feishu"),
            "content": "Full answer", "reply_to": "synthetic-parent",
            "metadata": {}, "images": [], "media_files": [],
            "local_files": ["/tmp/synthetic.png"],
        })
        assert returned[1] == "Full answer"
        assert hook_runtime._HFC_EXACT_COMPLETION_STAGE.get() is None
    else:
        assert await hook_runtime.emit_from_hermes_locals_async(values, "message.completed")
    assert len(posted) == (1 if mode == "stage" else 2)
    if mode != "stage":
        assert posted[0] is posted[1]
    assert posted[0]["data"]["rate"] == {"output_tps": 40.0, "basis": "request"}
    assert posted[0]["sequence"] == preview["sequence"]
    for field in ("answer", "tokens"):
        assert posted[0]["data"][field] == preview["data"][field]
    assert posted[0]["data"]["attachments"]
    assert posted[0]["data"]["native_delivery"] == "required"
    assert rt.take_observed_rate("synthetic-session") is None


@pytest.mark.parametrize("shape", ["python39", "python312", "generic"])
def test_observer_ast_contract_is_stable_across_python_fields(tmp_path, monkeypatch, shape):
    import ast
    path = tmp_path / "agent" / "turn_response_intake.py"
    path.parent.mkdir()
    path.write_text(_OBSERVER_SOURCE_CONTRACT)
    parse = ast.parse
    def version_shape(*args, **kwargs):
        tree = parse(*args, **kwargs)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if shape == "python39":
                    if hasattr(node, "type_params"):
                        del node.type_params
                else:
                    # Model 3.12+'s additional field even on older test Python.
                    if "type_params" not in node._fields:
                        node._fields = (*node._fields, "type_params")
                    node.type_params = [] if shape == "python312" else [ast.Name(id="T")]
        return tree
    monkeypatch.setattr(ast, "parse", version_shape)
    monkeypatch.setattr(ast, "dump", lambda *a, **k: pytest.fail("version-dependent ast.dump"))
    assert install.available(tmp_path) is (shape != "generic")


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_terminal", ["native", "failed"])
async def test_no_api_completion_cannot_borrow_previous_turn_rate(runtime, monkeypatch, previous_terminal):
    """Stable's preflight timeout returns before pre_llm_call, then Gateway adds session_id."""
    import ast
    rt, _ = runtime
    rt.handle_pre_llm_call(**observation())
    rt.handle_post_api_request(**observation())
    config = hook_runtime.RuntimeConfig(True, "http://127.0.0.1:12345/events", .2, 0, 0, 10)
    monkeypatch.setattr(hook_runtime, "load_runtime_config", lambda: config)
    monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda *_: None)
    allowed = [previous_terminal == "failed"]
    async def gate(*a, **kw):
        return hook_runtime._PolicyGateResult(allowed[0], None)
    async def flush(*a):
        pass
    posted = []
    async def post(url, payload, timeout):
        posted.append(payload)
        return {"ok": True, "applied": True}
    monkeypatch.setattr(hook_runtime, "_policy_gate_async", gate)
    monkeypatch.setattr(hook_runtime, "_flush_pending_deltas_for_local_vars", flush)
    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", post)
    await hook_runtime.emit_from_hermes_locals_async(
        completion_locals(), "message.failed" if previous_terminal == "failed" else "message.completed")
    # Source-derived return expressions from Hermes v0.22.2. The full raw-return
    # AST -> real run_sync common AST -> HFC entrypoint reproduction is retained
    # with the review evidence; these expressions keep the regression hermetic.
    source = '''
def _partial_turn_result(final_response, messages, api_call_count, **flags):
    return {"final_response": final_response, "messages": messages, "completed": False,
            "api_calls": api_call_count, "error": final_response, "partial": True, **flags}
result = _partial_turn_result(
    str(exc), list(conversation_history or []), 0,
    failed=True, compression_exhausted=True, turn_exit_reason="context_compression_timeout",
    failure_reason="context_overflow", failure_retryable=False)
'''
    namespace = {"exc": TimeoutError("Synthetic preflight timeout"), "conversation_history": []}
    exec(compile(ast.parse(source), "<stable-preflight-return>", "exec"), namespace)
    result = {**namespace["result"], "session_id": "synthetic-session"}
    allowed[0] = True
    assert await hook_runtime.emit_from_hermes_locals_async(completion_locals(
        message_id="synthetic-new-turn", response=result["final_response"], agent_result=result),
        "message.completed")
    assert "rate" not in posted[-1]["data"]
    assert posted[-1]["data"]["answer"] == result["final_response"]
    assert result["api_calls"] == 0 and result["failed"] is True
    # Purge the stale sample so its late callbacks cannot survive the no-API
    # terminal or starve the next legitimate observed turn as an overlap.
    rt.handle_post_api_request(**observation())
    next_values = {**observation(), "turn_id": "synthetic-next-turn", "api_request_id": "next-request"}
    rt.handle_pre_llm_call(**next_values)
    rt.handle_post_api_request(**next_values)
    actual = hook_runtime.build_event("message.completed", completion_locals(
        message_id="synthetic-next-message", agent_result={"api_calls": 1, "session_id": "synthetic-session"}))
    assert actual["data"]["rate"] == {"output_tps": 40.0, "basis": "request"}
    assert "rate" not in hook_runtime.build_event("message.completed", completion_locals())["data"]


@pytest.mark.parametrize("api_calls", [None, False, -1, "1", "absent"])
def test_unknown_invocation_api_count_discards_unowned_rate(runtime, api_calls):
    rt, _ = runtime
    rt.handle_pre_llm_call(**observation())
    rt.handle_post_api_request(**observation())
    result = {"session_id": "synthetic-session"}
    if api_calls != "absent":
        result["api_calls"] = api_calls
    assert "rate" not in complete(rt, agent_result=result)
    assert rt.take_observed_rate("synthetic-session") is None
