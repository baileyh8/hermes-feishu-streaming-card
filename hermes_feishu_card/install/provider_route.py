"""Optional, reversible capture at Hermes' actual agent-result construction."""
from __future__ import annotations

import ast
import re


_OLD_FUNCTIONS = {"run_sync", "_run_agent_inner"}
_V0230_FUNCTION = "_run_sync_scoped"
_V0230_CLASS = "TurnRunner"
_V0230_MODEL_EXPR = ast.dump(
    ast.parse('getattr(agent, "model", None) if agent else None', mode="eval").body
)
_V0230_PROVIDER_EXPR = ast.dump(
    ast.parse('getattr(agent, "provider", None) if agent else None', mode="eval").body
)
_V0230_LEDGER_EXPR = ast.dump(
    ast.parse(
        "{key: result.get(key) for key in _LEDGER_PASSTHROUGH_KEYS if key in result}",
        mode="eval",
    ).body
)
_RESOLVED_MODEL_EXPR = ast.dump(
    ast.parse('getattr(_agent, "model", None) if _agent else None', mode="eval").body
)


def _block(indent: str, newline: str, mode: str) -> str:
    if mode == "SCOPED_USAGE":
        return newline.join([
            f"{indent}# HERMES_FEISHU_CARD_PROVIDER_SCOPED_USAGE_BEGIN",
            f"{indent}_hfc_route = None",
            f"{indent}try:",
            f"{indent}    from hermes_feishu_card.hook_runtime import effective_response_route as _hfc_effective_route",
            f"{indent}    _hfc_route = _hfc_effective_route(agent)",
            f"{indent}except Exception:",
            f"{indent}    _hfc_route = None",
            f"{indent}if _hfc_route is not None:",
            f'{indent}    usage["model"], usage["provider"] = _hfc_route',
            f"{indent}else:",
            f'{indent}    usage["model"] = str(usage["model"] or "Unknown").split("/", 1)[-1] or "Unknown"',
            f'{indent}    usage["provider"] = ""',
            f"{indent}# HERMES_FEISHU_CARD_PROVIDER_SCOPED_USAGE_END",
            "",
        ])
    target, agent = ("usage[\"model\"]", "agent") if mode == "USAGE" else ("_resolved_model", "_agent")
    return newline.join([
        f"{indent}# HERMES_FEISHU_CARD_PROVIDER_{mode}_BEGIN",
        f"{indent}try:",
        f"{indent}    from hermes_feishu_card.hook_runtime import effective_response_model as _hfc_effective_model",
        f"{indent}    {target} = _hfc_effective_model({agent}) or {target}",
        f"{indent}except Exception:",
        f"{indent}    pass",
        f"{indent}# HERMES_FEISHU_CARD_PROVIDER_{mode}_END",
        "",
    ])


def remove_provider_route_patch(content: str) -> str:
    for mode in ("SCOPED_USAGE", "USAGE", "RESOLVED"):
        begin = f"# HERMES_FEISHU_CARD_PROVIDER_{mode}_BEGIN"
        end = f"# HERMES_FEISHU_CARD_PROVIDER_{mode}_END"
        if begin not in content and end not in content:
            continue
        lines = content.splitlines(keepends=True)
        starts = [i for i, line in enumerate(lines) if line.strip() == begin]
        ends = [i for i, line in enumerate(lines) if line.strip() == end]
        if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
            raise ValueError("corrupt provider route patch markers")
        a, b = starts[0], ends[0]
        indent = re.match(r"[ \t]*", lines[a]).group()
        newline = "\r\n" if lines[a].endswith("\r\n") else "\n"
        if "".join(lines[a:b + 1]) != _block(indent, newline, mode):
            raise ValueError("modified provider route patch")
        content = "".join(lines[:a] + lines[b + 1:])
    return content


def apply_provider_route_patch(content: str) -> str:
    clean = remove_provider_route_patch(content)
    tree = ast.parse(clean)
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    candidates = []
    for node in ast.walk(tree):
        if (not isinstance(node, ast.Assign) or len(node.targets) != 1
                or not isinstance(node.targets[0], ast.Name)):
            continue
        owner = _enclosing_function(node, parents)
        if owner is None:
            continue
        name = node.targets[0].id
        if owner.name in _OLD_FUNCTIONS and node in owner.body:
            if name == "_resolved_model" and ast.dump(node.value) == _RESOLVED_MODEL_EXPR:
                candidates.append((node, "RESOLVED"))
            elif name == "usage" and isinstance(node.value, ast.Dict):
                for key, value in zip(node.value.keys, node.value.values):
                    if (isinstance(key, ast.Constant) and key.value == "model"
                            and ast.dump(value) == _V0230_MODEL_EXPR):
                        candidates.append((node, "USAGE"))
        elif (owner.name == _V0230_FUNCTION and name == "usage"
              and _is_v0230_usage(node, parents)):
            candidates.append((node, "SCOPED_USAGE"))
    if len(candidates) != 1:
        return clean  # Older/unknown result shape: do not invent provenance.
    node, mode = candidates[0]
    lines = clean.splitlines(keepends=True)
    index = node.end_lineno
    if not lines[index - 1].endswith(("\n", "\r")):
        return clean
    indent = re.match(r"[ \t]*", lines[node.lineno - 1]).group()
    newline = "\r\n" if lines[index - 1].endswith("\r\n") else "\n"
    return "".join(lines[:index]) + _block(indent, newline, mode) + "".join(lines[index:])


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]):
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        current = parents.get(current)
    return None


def _enclosing_class(node: ast.AST, parents: dict[ast.AST, ast.AST]):
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.ClassDef):
            return current
        current = parents.get(current)
    return None


def _is_v0230_usage(node: ast.Assign, parents: dict[ast.AST, ast.AST]) -> bool:
    owner = _enclosing_function(node, parents)
    cls = _enclosing_class(owner, parents) if owner is not None else None
    if (owner is None or owner.name != _V0230_FUNCTION or cls is None
            or cls.name != _V0230_CLASS or not isinstance(node.value, ast.Dict)):
        return False
    model_match = provider_match = ledger_match = False
    for key, value in zip(node.value.keys, node.value.values):
        if isinstance(key, ast.Constant) and key.value == "model":
            model_match = ast.dump(value) == _V0230_MODEL_EXPR
        elif isinstance(key, ast.Constant) and key.value == "provider":
            provider_match = ast.dump(value) == _V0230_PROVIDER_EXPR
        elif key is None:
            ledger_match = ast.dump(value) == _V0230_LEDGER_EXPR
    return model_match and provider_match and ledger_match
