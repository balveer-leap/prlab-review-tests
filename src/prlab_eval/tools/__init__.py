from __future__ import annotations

from prlab_eval.tools.claude import ClaudeActionTool
from prlab_eval.tools.codeant import CodeAntTool
from prlab_eval.tools.coderabbit import CodeRabbitTool
from prlab_eval.tools.greptile import GreptileTool
from prlab_eval.tools.qodo import QodoTool

TOOLS = {
    GreptileTool.name: GreptileTool(),
    CodeRabbitTool.name: CodeRabbitTool(),
    QodoTool.name: QodoTool(),
    CodeAntTool.name: CodeAntTool(),
    # Same action, two setups: out of the box vs estate-aware prompt + sibling clones.
    "claude-plain": ClaudeActionTool("claude-plain"),
    "claude-skill": ClaudeActionTool("claude-skill"),
}


def get_tool(name: str):
    try:
        return TOOLS[name]
    except KeyError as exc:
        known = ", ".join(sorted(TOOLS))
        raise SystemExit(f"unknown tool {name!r}. registered: {known}") from exc
