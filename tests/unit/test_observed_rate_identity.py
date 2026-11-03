"""Unit tests verifying strict (session_id, turn_id) observation identity."""
from hermes_feishu_card.observed_rate import ObservedRates
from hermes_feishu_card import hermes_plugin_runtime as runtime_module


def observation(session_id="session-1", turn_id="turn-1", **changes):
    base = dict(
        session_id=session_id,
        turn_id=turn_id,
        platform="feishu",
        api_request_id=f"req-{turn_id}",
        model="model-1",
        provider="provider-1",
        usage={"input_tokens": 10, "output_tokens": 80},
        api_duration=2.0,
    )
    base.update(changes)
    return base


def test_same_session_different_turns_do_not_block_or_cross_consume():
    clock = [0.0]
    cache = ObservedRates(now=lambda: clock[0])

    # Turn 1 observations
    t1 = observation(session_id="s1", turn_id="t1", usage={"input_tokens": 10, "output_tokens": 80})
    cache.start(t1)
    cache.observe(t1)

    # Turn 2 observations in the exact same session arrive before turn 1 is taken
    t2 = observation(session_id="s1", turn_id="t2", usage={"input_tokens": 10, "output_tokens": 120})
    cache.start(t2)
    cache.observe(t2)

    # Taking t2 does not consume t1 and is not blocked by unconsumed t1
    res_t2 = cache.take("s1", "t2")
    assert res_t2 is not None
    rate2, tokens2 = res_t2
    assert tokens2 == 120
    assert rate2["output_tps"] == 60.0

    # Taking t1 succeeds with its own tokens
    res_t1 = cache.take("s1", "t1")
    assert res_t1 is not None
    rate1, tokens1 = res_t1
    assert tokens1 == 80
    assert rate1["output_tps"] == 40.0


def test_old_turn_samples_cannot_be_taken_by_new_turn():
    cache = ObservedRates()

    # Old turn unconsumed in session s1
    t_old = observation(session_id="s1", turn_id="old-turn", usage={"input_tokens": 10, "output_tokens": 80})
    cache.start(t_old)
    cache.observe(t_old)

    # New turn has no observations, queries cache for (s1, "next-turn")
    assert cache.take("s1", "next-turn") is None

    # Missing / None / empty turn_id fails closed without fallback
    assert cache.take("s1", None) is None
    assert cache.take("s1", "") is None
    assert cache.take(None, "next-turn") is None
    assert cache.take("", "next-turn") is None


def test_consume_once_and_late_duplicate_cannot_revive():
    cache = ObservedRates()

    t1 = observation(session_id="s1", turn_id="t1")
    cache.start(t1)
    cache.observe(t1)

    # First take succeeds
    assert cache.take("s1", "t1") is not None

    # Duplicate take fails
    assert cache.take("s1", "t1") is None

    # Late observe / duplicate post_api_request cannot revive consumed turn
    cache.observe(t1)
    assert cache.take("s1", "t1") is None

    # Duplicate start cannot revive either
    cache.start(t1)
    assert cache.take("s1", "t1") is None


def test_plugin_runtime_forwards_turn_id():
    posted = []
    rt = runtime_module.PluginRuntime(post=lambda *a: posted.append(a), observe_only=True)
    try:
        t1 = observation(session_id="s1", turn_id="t1")
        rt.handle_pre_llm_call(**t1)
        rt.handle_post_api_request(**t1)

        # Without turn_id, returns None (no fallback)
        assert rt.take_observed_rate("s1") is None

        # With wrong turn_id, returns None
        assert rt.take_observed_rate("s1", "wrong-turn") is None

        # With exact turn_id, returns rate
        rate = rt.take_observed_rate("s1", "t1")
        assert rate is not None
        assert rate[1] == 80

        # Consumed once
        assert rt.take_observed_rate("s1", "t1") is None
    finally:
        rt.close()



def test_terminal_before_timed_out_pre_callback_is_not_revived():
    cache = ObservedRates()
    assert cache.take("s1", "t1") is None
    late = observation(session_id="s1", turn_id="t1")
    cache.start(late)
    cache.observe(late)
    assert cache.take("s1", "t1") is None


def test_terminal_reclaims_retired_capacity_before_late_pre():
    cache = ObservedRates(capacity=1)
    assert cache.take("s1", "old") is None
    assert cache.take("s1", "new") is None
    late = observation(session_id="s1", turn_id="new")
    cache.start(late)
    cache.observe(late)
    assert cache.take("s1", "new") is None
    assert len(cache.entries) == 1
