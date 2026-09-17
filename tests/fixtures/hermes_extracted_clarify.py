"""Trimmed Hermes ``TurnRunner`` clarify seam with the extracted helper.

Mirrors ``gateway/run_turn_runner.py`` after Hermes ``242ff24ff7`` (2026-09-16):
the clarify body moved out of ``_clarify_callback_sync`` into
``_ask_clarify_question``, which both the single-question path and every batch
question go through, and the ``ctx = self._ctx`` binding moved with it.
"""


class TurnRunner:
    def __init__(self, runner, ctx):
        self._runner, self._ctx = runner, ctx

    def _setup_stream_consumer(self, platform_key):
        ctx = self._ctx
        stream_consumer = self._runner.stream_consumer
        return stream_consumer, ctx._run_still_current()

    def _clarify_callback_sync(self, question: str, choices, multi_select: bool = False,
                               questions=None) -> str:
        """Present a clarify prompt and block on a response (clarify_tool's contract)."""
        if questions:
            return self._clarify_batch_sync(questions)
        response, _answered = self._ask_clarify_question(question, choices, multi_select)
        return response

    def _clarify_batch_sync(self, questions) -> str:
        """Answer a batch: one card per question, stop at the first unanswered one."""
        payload = {"answers": {}, "timed_out": False}
        last = len(questions) - 1
        for index, entry in enumerate(questions):
            raw, answered = self._ask_clarify_question(
                entry.get("question", ""), entry.get("choices"), bool(entry.get("multi_select")),
                rearm=index == last)
            if not answered:
                payload.update(timed_out=True, notice=raw)
                break
            payload["answers"][entry.get("qid") or f"q{index}"] = raw
        return repr(payload)

    def _ask_clarify_question(self, question, choices, multi_select, rearm: bool = True) -> tuple[str, bool]:
        """One card: register, send, wait, then retire it (no answer) or re-arm (answer)."""
        ctx = self._ctx
        if not ctx._status_adapter:
            return "", False
        session_key = ctx.session_key or ""
        ctx._status_adapter.send_clarify(
            chat_id=ctx._status_chat_id, question=question, choices=choices,
            session_key=session_key)
        return ctx.wait_for_clarify(question, choices), True

    def _approval_notify_sync(self, approval_data):
        ctx = self._ctx
        ctx.send_approval(ctx.session_key or "", approval_data)
