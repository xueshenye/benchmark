"""Prompt templates for the simulated user (user-LLM).

The user-LLM role-plays a real user who is mid-conversation with a coding agent.
Two roles:

- ``build_user_message_prompt`` renders a milestone's ``user_intent`` into a
  natural, continuous user message (used for the initial task and for
  re-requesting a milestone after a forced advance).
- ``build_turn_decision_prompt`` asks the user-LLM to BOTH judge whether the
  agent's previous output satisfied the current milestone AND produce the next
  user message, as strict JSON ``{"satisfied": bool, "message": str}``.
"""

from __future__ import annotations

from benchmark.scenario import Milestone


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


def _evidence_section(workspace_evidence: str) -> str:
    """Render the workspace-diff evidence section, or nothing when absent."""
    if not workspace_evidence:
        return ""
    return f"\n【agent 本轮实际改动的工作区文件(diff)】\n{workspace_evidence}\n"


def _knowledge_section(milestone: Milestone | None) -> str:
    """Render the user's own knowledge (ground truth the user may reveal when
    the agent asks a clarifying question, but must not volunteer). Empty when
    the milestone has none."""
    if milestone is None or not milestone.user_knowledge:
        return ""
    return (
        "\n【你作为用户自己掌握的信息(不要主动透露)】"
        f"\n{milestone.user_knowledge}\n"
        "(只有当 agent 主动向你提问时,你才把其中相关的部分告诉它;agent 不问就不要说。)\n"
    )


def build_user_message_prompt(
    *,
    persona: str,
    milestone: Milestone,
    num_milestones: int,
    transcript: list[dict[str, str]],
    agent_output: str,
    workspace_evidence: str = "",
) -> str:
    """Build the prompt that asks the user-LLM to speak as the user for ``milestone``."""
    history = _format_transcript(transcript)
    return f"""你正在扮演一位真实用户,和一个编程 AI agent 进行多轮对话,共同完成一个编程任务。

你的角色:{persona}

【进度】这是第 {milestone.index}/{num_milestones} 个里程碑。
【你本轮想提出的要求(意图)】{milestone.user_intent}
【要求的方向(ground truth,仅供你把握,不要逐字照抄)】{milestone.requirement}
{_knowledge_section(milestone)}
【到目前为止的对话】
{history}

【agent 最近的实际输出】
{agent_output}
{_evidence_section(workspace_evidence)}
请以你的角色自然地说出你这一轮要对 agent 说的话。要求:
1. 像真实用户一样说话:口语化、简洁、连贯,可以自然地提及 agent 最近的做法(满意之处或问题)。
2. 你的这一轮要求必须让最终实现发生改变(是必要的修改/新增,而不是闲聊)。
3. 不要复述整段对话历史,不要解释你的思路,只输出你这一轮对 agent 说的话本身。
"""


def build_turn_decision_prompt(
    *,
    persona: str,
    current: Milestone,
    nxt: Milestone | None,
    num_milestones: int,
    transcript: list[dict[str, str]],
    agent_output: str,
    workspace_evidence: str = "",
    max_clarifications: int = 2,
) -> str:
    """Build the prompt that asks the user-LLM to judge the previous agent output
    against ``current`` and produce the next user message as strict JSON.

    The LLM must answer only with::

        {{"action": "judge"|"answer", "satisfied": true|false, "message": "<your natural speech>"}}

    ``action="answer"`` is the clarification sub-loop: when the agent's output
    is primarily clarifying questions, the user answers them (from its own
    ``user_knowledge``) instead of judging; the controller keeps the interaction
    on the SAME milestone without consuming a correction. ``action="judge"``
    (default) is the normal judgment: ``satisfied=false`` → ``message`` is
    concrete corrective feedback for the current milestone; ``satisfied=true`` →
    ``message`` naturally requests the next milestone's ``user_intent`` (or is a
    closing remark if no next exists).

    ``workspace_evidence`` is a diff of the files the agent actually changed, so
    the judge checks real code rather than the agent's self-report.
    """
    history = _format_transcript(transcript)
    if nxt is not None:
        next_hint = (
            f"【如果你满意,你接下来想提出的要求(意图)】{nxt.user_intent}"
            f"\n(这一阶段是第 {nxt.index}/{num_milestones} 个里程碑)"
        )
    else:
        next_hint = "【如果你满意,任务已完成,你只需要自然收尾,不用提出任何新要求】"
    return f"""你正在扮演一位真实用户,和一个编程 AI agent 进行多轮对话,共同完成一个编程任务。

你的角色:{persona}

【当前阶段】第 {current.index}/{num_milestones} 个里程碑。
【这个阶段你要求 agent 做到的事(意图)】{current.user_intent}
【完成标准(ground truth,仅供你判断,不要逐字照抄)】{current.requirement}
{_knowledge_section(current)}
{next_hint}

【到目前为止的对话】
{history}

【agent 最近一轮的实际输出】
{agent_output}
{_evidence_section(workspace_evidence)}
请先判断:agent 最近一轮的输出是否已经让"当前阶段"的要求得到满足?

然后以你的角色说出你这一轮要对 agent 说的话,并严格输出如下 JSON(不要输出其他任何内容):

{{"action": "judge"|"answer", "satisfied": true|false, "message": "<你自然说出的话>"}}

规则:
1. 只有当前阶段的要求确实满足,才给 true;否则 false。
2. satisfied=false 时,message 必须是针对 agent 实际输出缺点的具体纠正意见(指出哪里不对/缺什么),并且必须让最终实现发生改变。
3. satisfied=true 且还有下一阶段时,message 应自然、口语化地提出下一阶段的要求;satisfied=true 且没有下一阶段时,message 是自然收尾语。
4. 像真实用户一样说话,可以提及 agent 最近的做法,但不要复述整段历史。
5. 提出的要求以【完成标准】和【意图】为准,不要凭空添加其中没有的新字段/新格式/新硬性约束(真实用户不会发明需求);确有必要增加时,要在 message 里给一句理由。
6. 澄清(新增):如果 agent 最近一轮的输出主要是向你提问(比如问知识库在哪、用什么实现、回答不了怎么办、接口怎么调用等),而你还没把这些告诉它(本阶段已澄清次数 < {max_clarifications}),那么**不要判定、不要推进**——直接以你的角色自然、真实地回答它,一次把 agent 问的都答清楚,并输出 {{"action": "answer", "satisfied": false, "message": "<你的回答>"}}。回答只能透露"你作为用户自己掌握的信息"里有的内容,不要编造。只有当 agent 反复只问不做(已超过 {max_clarifications} 次)时,才停止回答,改输出 {{"action": "judge", "satisfied": false, "message": "<纠正意见,要求它基于合理假设继续实现>"}}。
7. 像真实用户一样,你是真的"上手用过" agent 做出来的东西的(亲手跑过命令、看过输出、打开过概览/页面),判定要基于实际使用体验,而不是只看它嘴上怎么说。在合适时机(通常是一个里程碑达成时),**自然地给出至少一条关于用户体验的改进建议**——可以针对用户界面/易用性、编码流畅度,或功能完整度(例如"status 需要把日程按日期排个序""请给命令加个 -h 帮助""同事说 autocomplete 能带上函数签名提示"之类)。建议要口语化、像真实用户的期望,基于你实际试用时的真实感受;verifier 不检查它,agent 采纳与否都不影响这一轮是否达标,所以不要因此把 satisfied 判成 false。如果这次对话还没给过任何建议,尽量在合适的时机(通常是某个里程碑达成时)给一条;给过了就不用重复。
"""
