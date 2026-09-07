import asyncio
from contextvars import ContextVar
from types import SimpleNamespace
import sys

import pytest

from hermes_feishu_card import hook_runtime, runner
from hermes_feishu_card.install.provider_route import apply_provider_route_patch, remove_provider_route_patch


async def test_multiplex_profiles_keep_task_identity_despite_global_override(monkeypatch):
    home = ContextVar("home")
    monkeypatch.setitem(sys.modules, "hermes_constants", SimpleNamespace(get_hermes_home=home.get))
    monkeypatch.setenv("HERMES_FEISHU_CARD_PROFILE_ID", "default")
    runtime = SimpleNamespace(config=SimpleNamespace(multiplex_profiles=True))

    async def turn(name):
        home.set(f"/home/user/.hermes/profiles/{name}")
        await asyncio.sleep(0)
        explicit = hook_runtime._profile_identity({"self": runtime}, SimpleNamespace(profile=name), None)
        scoped = hook_runtime._profile_identity({"self": runtime}, None, None)
        return explicit, scoped

    assert await asyncio.gather(turn("ai-secretary"), turn("engineering")) == [
        (("ai-secretary", "locals"), ("ai-secretary", "hermes_home")),
        (("engineering", "locals"), ("engineering", "hermes_home")),
    ]


def test_named_only_boundary_does_not_fall_back_to_another_bot():
    boundary = runner.build_feishu_boundary({"profiles": {
        "ai-secretary": {"feishu": {"app_id": "test", "app_secret": "test"}},
    }})
    assert boundary.router(SimpleNamespace(data={"profile_id": "ai-secretary"}, chat_id="test")).bot_id == "default"
    for identity in ("", "default", "missing"):
        with pytest.raises(ValueError, match="profile_unknown"):
            boundary.router(SimpleNamespace(data={"profile_id": identity}, chat_id="test"))


@pytest.mark.parametrize("route,expected", [
    (("fallback-model", "backup"), "backup/fallback-model"),
    (None, "primary/primary-model"),
    (("backup/model", "backup"), "backup/model"),
    (("model", "https://secret.example"), "model"),
])
def test_effective_model_survives_primary_runtime_restoration(route, expected):
    agent = SimpleNamespace(model="primary-model", provider="primary", _provider_fallback_route=route)
    assert hook_runtime.effective_response_model(agent) == expected


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_provider_patch_executes_at_result_construction_and_restores(newline):
    source = newline.join([
        "def run_sync(agent):",
        '    usage = {"model": getattr(agent, "model", None) if agent else None}',
        '    return {"final_response": "answer", **usage}', "",
    ])
    patched = apply_provider_route_patch(source)
    assert patched != source
    assert apply_provider_route_patch(patched) == patched
    assert remove_provider_route_patch(patched) == source
    namespace = {}
    exec(compile(patched, "fixture.py", "exec"), namespace)
    agent = SimpleNamespace(model="primary", provider="initial", _provider_fallback_route=("actual", "backup"))
    assert namespace["run_sync"](agent)["model"] == "backup/actual"


def test_provider_patch_legacy_result_and_corrupt_marker():
    source = 'def run_sync(_agent):\n    _resolved_model = getattr(_agent, "model", None) if _agent else None\n    return {"model": _resolved_model}\n'
    patched = apply_provider_route_patch(source)
    assert remove_provider_route_patch(patched) == source
    with pytest.raises(ValueError, match="modified provider"):
        remove_provider_route_patch(patched.replace("    pass", "    raise"))
