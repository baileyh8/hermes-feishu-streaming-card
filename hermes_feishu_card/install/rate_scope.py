"""Exact optional Gateway projection of native result identity for display metrics."""
from __future__ import annotations

import ast
from hashlib import sha256

# Retain all previously verified legacy-observer pins; identical complete rows share a cohort.
COHORTS = ({'agent/conversation_loop.py': '926435d563ef1b7de1049f5c36897f1cda7376508b98164a68a0cb5192656270',
  'agent/turn_context.py': '1fdc08f930d6f76825c5b06f14377768c31f2178e9d893dc0bed10911be33991',
  'agent/turn_facade.py': 'e9176a8ae1d7822fa45c75b680d5ae4be7c949e49c877673aea867cc146856a0',
  'agent/turn_finalizer.py': '9000e17a4a0dba03b1d97cc10fe4184addcca685520ca0c9e6c6ef2cb97d1f25',
  'gateway/run_turn_runner.py': '6ec879a91aa09765515a69368c886d1ce4d3174b159868c966e8740e9cf1b347'},
 {'agent/conversation_loop.py': '36dd6f02b169a544a95598dee74cb775914420dc700cc88cc946effd103b72bc',
  'agent/turn_context.py': '09a6159b0301da271f1a6bdcf15d924a05dee8d7d6b9e2f4ed46e406aac0aa4e',
  'agent/turn_facade.py': 'e9176a8ae1d7822fa45c75b680d5ae4be7c949e49c877673aea867cc146856a0',
  'agent/turn_finalizer.py': '4c1e83c6a23bcb7fc10f942306459b54b130975fdce3d76d8fd7f2432698632e',
  'gateway/run_turn_runner.py': '04f76f4857a8ba251b014d615b814f3920fef8212ad24e69b28fbbc4fdf2df39',
  'gateway/session_cli_turn.py': '86e16dbefe035b07474465f5c8854cac227c723205ab46559a686391a3cd3032'},
 {'agent/conversation_loop.py': 'bc1a44a42e254c9500cee099aa0d81590534aecb0ae6986f0f5bce6f24849d49',
  'agent/turn_context.py': '6dd3b458d8368c9ff98359db77d448592a45dfdbaef199b93a0b312608f48641',
  'agent/turn_facade.py': 'e9176a8ae1d7822fa45c75b680d5ae4be7c949e49c877673aea867cc146856a0',
  'agent/turn_finalizer.py': '44d5b86f2f76c8668cf28ac853b3a29347223ece9e1b3d4ce3abf2fddd1ae49c',
  'gateway/run_turn_runner.py': '1fbbca238907f365896256ce2c73a68e24b3c70559de6f8034ddca45fa219e9a',
  'gateway/session_cli_turn.py': '0aa362fdebed89074dd2b091c3286938360bdf3cb3a5dc5c7b0b21433a96d1ac'},
 {'agent/conversation_loop.py': '5a2b1973189300f0f07a990e39c6f5ddf200288aaec1032205685bef3437e4bf',
  'agent/turn_context.py': '09a6159b0301da271f1a6bdcf15d924a05dee8d7d6b9e2f4ed46e406aac0aa4e',
  'agent/turn_facade.py': 'e9176a8ae1d7822fa45c75b680d5ae4be7c949e49c877673aea867cc146856a0',
  'agent/turn_finalizer.py': '4c1e83c6a23bcb7fc10f942306459b54b130975fdce3d76d8fd7f2432698632e',
  'gateway/run_turn_runner.py': '6cf9f1acdd64d69f5c613be214695b33dafe215eeced68cebe53f5d0c5e4cbc9',
  'gateway/session_cli_turn.py': '86e16dbefe035b07474465f5c8854cac227c723205ab46559a686391a3cd3032'},
 {'agent/conversation_loop.py': '04e078c88a02499f9b871e0e72b3d20f4415916a46537b2f38d1d694e2c667e5',
  'agent/turn_context.py': '4f620a921573d1b28b9bf5704f49500ef1046b3e259a2281404fda53ee939753',
  'agent/turn_facade.py': 'e9176a8ae1d7822fa45c75b680d5ae4be7c949e49c877673aea867cc146856a0',
  'agent/turn_finalizer.py': 'c9a2dc3a56050172af35fd71dfec43a12071df016daa970d0da42631a5b0a4ab',
  'gateway/run_turn_runner.py': '23fee696d5090fbef9d1841c4eccaea9cb93b4204cc9dcf4c2a49ac198a50aaf',
  'gateway/session_cli_turn.py': '0aa362fdebed89074dd2b091c3286938360bdf3cb3a5dc5c7b0b21433a96d1ac'},
 {'agent/conversation_loop.py': '5a2b1973189300f0f07a990e39c6f5ddf200288aaec1032205685bef3437e4bf',
  'agent/turn_context.py': '800fdfe776113120cdd0f0d454f5e7f1c68c4573fcf985980b5e6a873ec22a04',
  'agent/turn_facade.py': 'e9176a8ae1d7822fa45c75b680d5ae4be7c949e49c877673aea867cc146856a0',
  'agent/turn_finalizer.py': '9000e17a4a0dba03b1d97cc10fe4184addcca685520ca0c9e6c6ef2cb97d1f25',
  'gateway/run_turn_runner.py': '66f4e768959343e4dd52a0b9d7f5937f9b8a5ef292fd78659cc8aae79e44fcc1',
  'gateway/session_cli_turn.py': '1e7a48eb3b64d786907f5230453da7156ace3743f16b3a3724a6f0f5c4df87a6'})
BEGIN = "# HERMES_FEISHU_CARD_RATE_SCOPE_BEGIN"
END = "# HERMES_FEISHU_CARD_RATE_SCOPE_END"
CAPTURE_BEGIN = "# HERMES_FEISHU_CARD_RATE_CAPTURE_BEGIN"
CAPTURE_END = "# HERMES_FEISHU_CARD_RATE_CAPTURE_END"
LINES = (BEGIN, "try:",
         "    from hermes_feishu_card.native_rate_scope import project",
         "    project(locals())", "except Exception:", "    pass", END)
CALLS = ("return agent.run_conversation(api_message, **kwargs)",
         "result = agent.run_conversation(api_message, **kwargs)")


def call_lines(original: str) -> tuple[str, ...]:
    prefix = "return " if original.startswith("return ") else "result = "
    return (CAPTURE_BEGIN, "_hfc_rate_call = agent.run_conversation", "try:",
            "    from hermes_feishu_card.native_rate_scope import observe_call as _hfc_rate_observe",
            "except Exception:", "    _hfc_rate_observe = None",
            prefix + "(_hfc_rate_call(api_message, **kwargs) if _hfc_rate_observe is None else "
            "_hfc_rate_observe(_hfc_rate_call, locals(), api_message, **kwargs))", CAPTURE_END)


def anchor(content: str) -> int | None:
    tree = ast.parse(content)
    owners = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TurnRunner"]
    if len(owners) != 1:
        return None
    nodes = [n for fn in owners[0].body if isinstance(fn, ast.FunctionDef)
             and fn.name in {"run_sync", "_run_sync_scoped"}
             for n in fn.body if isinstance(n, ast.Assign)
             and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
             and n.targets[0].id == "common" and isinstance(n.value, ast.Dict)]
    return nodes[0].end_lineno if len(nodes) == 1 else None


def call_anchor(content: str) -> int | None:
    tree = ast.parse(content)
    nodes = [n for cls in tree.body if isinstance(cls, ast.ClassDef) and cls.name == "TurnRunner"
             for fn in cls.body if isinstance(fn, ast.FunctionDef)
             and fn.name == "_run_conversation_with_approval" for n in ast.walk(fn)
             if isinstance(n, (ast.Return, ast.Assign)) and isinstance(n.value, ast.Call)
             and ast.unparse(n.value.func) == "agent.run_conversation"]
    if len(nodes) != 1 or nodes[0].lineno != nodes[0].end_lineno:
        return None
    index = nodes[0].lineno - 1
    return index if content.splitlines()[index].strip() in CALLS else None


def _block(content: str, begin: str, end: str) -> tuple[list[str], int, int, str] | None:
    lines = content.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == begin]
    ends = [i for i, line in enumerate(lines) if line.strip() == end]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise ValueError("unsafe rate scope markers")
    start, finish = starts[0], ends[0]
    indent = lines[start][:len(lines[start]) - len(lines[start].lstrip())]
    return lines, start, finish, indent


def remove(content: str) -> str:
    block = _block(content, BEGIN, END)
    if block is not None:
        lines, start, finish, indent = block
        if [line.rstrip("\r\n") for line in lines[start:finish + 1]] != [indent + line for line in LINES]:
            raise ValueError("unsafe rate scope body")
        del lines[start:finish + 1]
        content = "".join(lines)
        if anchor(content) != start:
            raise ValueError("unsafe rate scope placement")
    block = _block(content, CAPTURE_BEGIN, CAPTURE_END)
    if block is not None:
        lines, start, finish, indent = block
        found = [original for original in CALLS
                 if [line.rstrip("\r\n") for line in lines[start:finish + 1]] ==
                 [indent + line for line in call_lines(original)]]
        if len(found) != 1:
            raise ValueError("unsafe rate capture body")
        newline = "\r\n" if lines[start].endswith("\r\n") else "\n"
        lines[start:finish + 1] = [indent + found[0] + newline]
        content = "".join(lines)
        if call_anchor(content) != start:
            raise ValueError("unsafe rate capture placement")
    return content


def apply(clean: str) -> str:
    clean = remove(clean)
    digest = sha256(clean.replace("\r\n", "\n").encode()).hexdigest()
    index = call_anchor(clean)
    if (index is None or anchor(clean) is None
            or not any(row["gateway/run_turn_runner.py"] == digest for row in COHORTS)):
        return clean
    lines = clean.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in clean else "\n"
    indent = lines[index][:len(lines[index]) - len(lines[index].lstrip())]
    lines[index:index + 1] = [indent + line + newline for line in call_lines(lines[index].strip())]
    patched = "".join(lines)
    project_index = anchor(patched)
    if project_index is None:
        return clean
    lines = patched.splitlines(keepends=True)
    indent = lines[project_index - 1][:len(lines[project_index - 1]) - len(lines[project_index - 1].lstrip())]
    lines[project_index:project_index] = [indent + line + newline for line in LINES]
    return "".join(lines)
