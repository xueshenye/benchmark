"""Prompt templates for the simulated user (user-LLM).

The user-LLM role-plays a real user who is mid-conversation with a coding agent.
It turns each round's scripted ``user_intent`` into a natural, continuous user
message that references the agent's actual output from the previous round.
"""

from __future__ import annotations

from benchmark.scenario import Round


def _format_transcript(transcript: list[dict[str, str]]) -> str:
    if not transcript:
        return "(conversation has not started yet)"
    lines = []
    for turn in transcript:
        role = turn.get("role", "?")
        content = turn.get("content", "")
        label = "用户" if role == "user" else "Agent"
        lines.append(f"[{label}]\n{content}")
    return "\n\n".join(lines)


def build_user_message_prompt(
    *,
    persona: str,
    round_spec: Round,
    num_rounds: int,
    transcript: list[dict[str, str]],
    agent_output: str,
) -> str:
    """Build the prompt that asks the user-LLM to produce the next user message."""
    history = _format_transcript(transcript)
    return f"""你正在扮演一位真实用户,和一个编程 AI agent 进行多轮对话,共同完成一个编程任务。

你的角色:{persona}

【进度】当前是第 {round_spec.index}/{num_rounds} 轮。
【你本轮想提出的要求(意图)】{round_spec.user_intent}
【要求的方向(ground truth,仅供你把握,不要逐字照抄)】{round_spec.requirement}

【到目前为止的对话】
{history}

【agent 上一轮的实际输出】
{agent_output}

请以你的角色自然地说出你这一轮要对 agent 说的话。要求:
1. 像真实用户一样说话:口语化、简洁、连贯,可以自然地提及 agent 上一轮的做法(满意之处或问题)。
2. 你的这一轮要求必须让最终实现发生改变(是必要的修改/新增,而不是闲聊)。
3. 不要复述整段对话历史,不要解释你的思路,只输出你这一轮对 agent 说的话本身。
"""
