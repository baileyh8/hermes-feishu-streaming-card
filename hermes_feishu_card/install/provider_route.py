"""Optional, reversible capture at Hermes' actual agent-result construction."""
from __future__ import annotations

import ast
import re


def _block(indent: str, newline: str, mode: str) -> str:
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
    for mode in ("USAGE", "RESOLVED"):
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
    candidates = []
    for owner in ast.walk(tree):
        if not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)) or owner.name not in {"run_sync", "_run_agent_inner"}:
            continue
        for node in owner.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            name = node.targets[0].id
            if name == "_resolved_model" and ast.dump(node.value) == ast.dump(ast.parse('getattr(_agent, "model", None) if _agent else None', mode="eval").body):
                candidates.append((node, "RESOLVED"))
            elif name == "usage" and isinstance(node.value, ast.Dict):
                for key, value in zip(node.value.keys, node.value.values):
                    if isinstance(key, ast.Constant) and key.value == "model" and ast.dump(value) == ast.dump(ast.parse('getattr(agent, "model", None) if agent else None', mode="eval").body):
                        candidates.append((node, "USAGE"))
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
