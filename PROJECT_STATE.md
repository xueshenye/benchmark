# PROJECT_STATE.md — 项目进展与交接文档

> 本文件是**新会话的第一入口**。先读 `CLAUDE.md`(章程/框架参考),再读本文件(当前进展/计划/踩坑)。更新日期:2026-08-11。

## 1. 项目目标与核心设计

在 [Harbor](https://github.com/laude-institute/harbor) 0.20.0 上构建**编程领域 Multi-turn 交互式 Agent Benchmark**。

与普通单轮任务的区别:**"用户"在 agent 完成任务过程中多次介入**,由一个独立 LLM(**user-LLM**)根据 agent 上一轮的实际输出,生成下一轮自然的 user 消息。评测 agent 是否:持续理解上下文、正确更新需求、不遗忘旧约束(回归)、最终完成编程任务。reward 可用于 RLVR。

**每轮 instruction 只包含模拟用户那一条自然消息(不重放历史)** —— agent 必须从环境(已有代码)+ 新消息重建上下文。

**介入是动态的(Design A+)**:**user-LLM 每轮返回 `{"satisfied": bool, "message": str}` 判定上轮输出是否满足当前里程碑** —— 满意→推进下一里程碑(message=下一需求);不满意→纠正轮(留在当前,≤ `max_corrections`);纠正耗尽→强制推进(该里程碑由 verifier 判 0)。总轮次由 `max_rounds` 硬上限约束,不再预写固定。

**核心判别(需求 #5)**:`reward = 各轮得分乘积`(稀疏 0/1)。只完成最后一轮的 agent → 早期轮 = 0 → reward=0;完整多轮的 agent → reward=1。

**reward 协议(与需求原文的偏差)**:需求写的是 `/logs/verifier/rewards.txt`,但 Harbor 0.20.0 verifier 只读 `reward.txt`(标量)或 `reward.json`(扁平 dict str→number)。本项目用 **`/logs/verifier/reward.json`**:`{"round_1":0|1, ..., "reward":<乘积>}`。多键 reward 是产品,documentation 里记录的 `rewards.json`/`rewards.txt` 均不存在,勿用。

## 2. 当前架构(Design A+ :自定义 agent,单 trial,动态里程碑)

```
harbor run -e novita --env-file .env -p <task> -a benchmark.interactive_agent:InteractiveUserClaude -m <model>
  │ agent.run() [宿主进程,PYTHONPATH=. 导入 benchmark/]
  │   读容器内 scenario.json(exec cat /workspace/scenario.json)
  │   TurnController 驱动动态轮次:
  │   Round 1: instruction = instruction.md(初始任务)→ claude --print 执行 → 捕获输出
  │   每轮后: 抓取 /workspace 快照 → diff(上轮 vs 本轮)→ 作为"真实改动证据"
  │   后续轮: user-LLM(LiteLLM)据「上轮输出 + 工作区 diff」判定 → 严格 JSON {"satisfied": bool, "message": str}
  │     satisfied=true  → 推进下一里程碑(message = 下一需求)
  │     satisfied=false → 纠正轮,留在当前(≤ max_corrections)
  │     纠正耗尽        → 强制推进(该里程碑由 verifier 判 0)
  │     → instruction = 仅 message → claude --print 执行 → 捕获输出
  │   写 interactive_transcript.json 工件(scenario + decisions[含 workspace_evidence] + transcript)
  └─ 结束后 → verifier(tests/scorer.py,容器内)对最终状态逐里程碑累计检查 → 写 reward.json
```

- 轮次共享同一沙箱,前几轮代码保留,后续轮次在其上修改;**总轮次动态**(`max_rounds` 硬上限)。
- agent 注册:`-a benchmark.interactive_agent:InteractiveUserClaude`(Harbor import-path 工厂,已验证可实例化)。
- 文件布局:`benchmark/{interactive_agent,controller,user_simulator,scenario,prompt_templates}.py`;`tasks/benchmark/multi-round-cli-demo/`;`tests/`(无 Docker 单元测试,含 controller/scorer)。

## 3. 环境与执行(踩坑后确定)

- uv venv:`.venv`(Python 3.12.13);Harbor CLI `.venv/bin/harbor`(0.20.0)。**用 `uv sync`,不要 `pip install harbor`**(镜像源过期)。
- **执行 provider = Novita 云沙箱**(`harbor run -e novita`)。本机 Docker daemon 虽在运行,但当前用户无 root/socket 权限且无 sudo → 无法本地 Docker。
- **SDK**:`novita-sandbox` 已装(`pyproject.toml` 主依赖)。Harbor 对 cloud provider 懒加载。
- Novita 把任务 `environment/Dockerfile` 在云端构建成模板(支持 FROM/RUN/COPY/ADD/WORKDIR/USER/ENV/CMD),模板按 `environment_name+hash+key尾` 缓存;**scenario.json 变了会重建模板(几分钟)**。沙箱最长 1h 自动销毁,按运行时长 + LLM 调用计费。
- **运行凭证**在 `.env`(已 git-ignore):
  - `NOVITA_API_KEY` — 沙箱 + user-LLM 计费;**账号必须有余额**,否则 403 NOT_ENOUGH_BALANCE。
  - `USER_LLM_MODEL=openai/moonshotai/kimi-k3`、`USER_LLM_API_BASE=https://api.novita.ai/openai`、`USER_LLM_API_KEY=<同 Novita key>` — user-LLM 走 Novita 的 OpenAI 兼容端点。**模型名必须带 LiteLLM 认识的 provider 前缀**:`openai/<model-id>`,`moonshotai/kimi-k3`(裸前缀)会让 LiteLLM provider 解析崩溃。
  - `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`、`ANTHROPIC_AUTH_TOKEN=<DeepSeek key>` — **agent(claude-code)在沙箱里的后端**。host 的 Claude OAuth 不会带进沙箱;没有这两项 claude 报 `Not logged in` → agent error → verifier 全 0。DeepSeek 是 Anthropic 兼容代理;agent 模型用 `-m deepseek-v4-flash`(宿主 Claude 同款);实测 `-m claude-sonnet-5` 也被 DeepSeek 端点接受。
  - `--env-file .env` 会把上述变量 load 进 harbor 进程 os.environ;agent 的 `ANTHROPIC_*` 从 `claude_code.py` 读 os.environ/`--ae`(extra_env)。
- `.env.example` 是模板(含各变量的获取方式),`.env` 不提交。

## 4. 当前演示任务(`tasks/benchmark/multi-round-cli-demo/`)

3 轮累计场景(stats CLI):基础摘要 → `--output-json`(明确要求**总是数组**)→ 多文件(保留 1、2 轮功能)。

**Round 1 初始任务(instruction.md,轮 1 的 instruction):**
```
# Stats CLI

在 `/workspace` 中创建一个名为 `stats` 的命令行工具(入口脚本 `/workspace/stats`,或 `/workspace/src/stats.py`,确保命令 `stats <input.csv>` 可以直接运行)。

用法:
```
stats <input.csv>
```

它读取指定的 CSV 文件(第一行为表头),对**第一个数值列**计算:
- `count`(有效数值个数)
- `mean`(均值)
- `min`(最小值)
- `max`(最大值)

并打印一行人类可读的摘要,例如:
```
count=5 mean=3.0 min=1.0 max=5.0
```

要求:
- 用 Python 实现(仅用标准库,不依赖第三方库)。
- 文件不存在时打印清晰的错误信息并以非零码退出。
- 在 `/workspace` 下给出实现文件,并确保可以直接运行。
```

**scenario.json(environment/,会烘焙进容器 /workspace/scenario.json,verifier 与 agent 都读它;Design A+ 新 schema):**
```json
{
  "user_persona": "一位数据产品经理,说话简洁直接,很在意 CLI 的向后兼容和易用性。",
  "milestones": [
    {"index":1, "requirement":"stats CLI 能读取单个 CSV 文件并打印 count/mean/min/max 统计摘要",
     "user_intent":"让 agent 从零实现一个处理 CSV 的 stats 命令行工具", "test_id":"stats_basic"},
    {"index":2, "requirement":"新增 --output-json 选项:JSON 输出必须是 JSON 数组,每个元素包含 count/mean/min/max 四个字段;即使只有一个输入文件,也输出长度为 1 的数组;默认仍为纯文本输出,不带该选项时行为不变",
     "user_intent":"在现有 stats 工具上加一个 --output-json 选项,并要求 JSON 输出总是数组格式(单文件也输出 [{...}] 数组)", "test_id":"stats_json"},
    {"index":3, "requirement":"支持多个输入文件参数,对每个文件分别输出统计;纯文本模式每个文件一行;JSON 模式输出数组,每个元素对应一个文件;保留单文件、纯文本与 JSON 输出等已有行为(单文件 JSON 仍为长度 1 的数组)",
     "user_intent":"让 stats 支持一次处理多个文件,且不能破坏已有的功能,JSON 输出保持数组格式", "test_id":"stats_multi"}
  ],
  "max_rounds": 6,
  "max_corrections": 1
}
```

**user-LLM 提示词**(`benchmark/prompt_templates.py`):
- `build_turn_decision_prompt`(每轮 ≥2 的判定):给 LLM persona + 当前里程碑 `requirement`("ground truth,仅供把握,不要逐字照抄")+ `user_intent` + 下一里程碑 `user_intent`(若有)+ 对话历史 + agent 上一轮实际输出;要求先判定 agent 输出是否满足当前里程碑,再输出**严格 JSON** `{"satisfied": bool, "message": "<自然用户话语>"}`。不满意→message 为具体纠正;满意且有下一里程碑→message 为下一需求;满意且无下一里程碑→自然收尾。
- `build_user_message_prompt`(强制推进时重请求下一里程碑):与 Design A 相同,但针对 `Milestone`。
- **注意:`user_intent` 是 user-LLM 主要转述的内容,ground-truth 格式细节必须同时写进 `user_intent` 才可靠传达**(踩坑,见 §6)。

**控制器**(`benchmark/controller.py` 的 `TurnController`):传输无关状态机。每轮按判定推进/纠正/强制推进,`max_rounds` 硬上限;`decisions` 逐轮记录(satisfied/forced_advance/milestone_index)进 `interactive_transcript.json` 供 RLVR 诊断。

**verifier**(`tests/scorer.py`):`CHECKERS` 按 `test_id` 映射检查函数;对**最终** workspace 跑 3 轮检查 → 写 `/logs/verifier/reward.json`。`check_stats_json` 要求 `--output-json <单文件>` 输出 `[{count,mean,min,max}]`(list-of-one);`check_stats_multi` 要求多文件 JSON 为数组(长度=文件数)。

**参考解法**(`solution/solve.sh`):`--output-json` 永远 `json.dumps(results)`(数组,单文件也 `[{...}]`);纯文本每行 `file: count=...`。本地验证 3 轮全 1。

## 5. 验证结果(截至 2026-08-11)

| 验证 | 结果 |
|---|---|
| 单元测试 `.venv/bin/python -m pytest tests/`(Design A) | ✅ 12/12 |
| 单元测试(Design A+,含 controller/scorer/parse + evidence) | ✅ 36/36 |
| 参考解法 → scorer(本地,无容器) | ✅ round_1,2,3=1,reward=1 |
| "只完成后面轮次"实现 → scorer(本地) | ✅ round_1=0,2=1,3=1 → reward=0(判别器有效) |
| 端到端 run #1(14:00) | ❌ errored — `AgentAuthenticationError`(沙箱 claude "Not logged in")→ 修复(§6) |
| 端到端 run #2(14:08) | ✅ 完成,0 error;**round_1=1, round_2=0, round_3=1 → reward=0**。诊断:round_2 ground-truth 格式歧义(§6)→ 修 scenario.json |
| 端到端 run #3(14:23) | ✅ **round_1,2,3=1 → reward=1**。显式格式规格修复生效:Kimi 把"总是数组"完整转述进 round_2/round_3 自然消息,agent 按数组实现,三轮全过 |
| Design A+ 动态交互(单测) | ✅ 满意推进/纠正轮/强制推进/轮次上限均有测试覆盖(scorer 首获单测覆盖) |
| 端到端 run A+ #1(02:23) | ✅ **round_1,2,3=1,reward=1**。Design A+ 首次端到端:Kimi 3 次判定全 `satisfied:true`,无纠正轮;自然消息引用 agent 实际做法 |
| 端到端 run A+ #2(02:56,含工作区证据) | ✅ **round_1,2,3=1,reward=1**。每轮 `decisions[].workspace_evidence` 记录真实文件 diff(新增/修改 /workspace/stats),判定基于实际改动;无畸形判定警告 |
| 端到端判别器(03:40,`LastOnlyClaude`) | ✅ **round_1=0,round_2=0,round_3=1 → reward=0**。确定性"只做最后里程碑"agent(只写多文件实现)→ verifier 在真实 Novita 部署下正确判 0 |
| 端到端 Design B(04:17,原生 multi-step) | ✅ **reward=1,round_1,2,3=1**。插件注入→`InteractiveMultiStepTrial`→步间 runner 整条链打通;实际只跑 **3 步**(提前终止生效);per-step reward 稠密诊断(1,0,0→0 / 1,1,0→0 / 1,1,1→1),每步轨迹保留 |

run #2 证明整条流水线(Design A + Novita + DeepSeek 后端 + Kimi user-LLM)在真实部署下工作;round_2=0 不是 harness bug,是任务规格问题。run #3 证明 **ground-truth 显式化 → user-LLM 忠实转述 → agent 正确实现**的链路成立:把格式/约束细节写进 `requirement`+`user_intent`,Kimi 会原样传达(甚至补充理由),agent 据此实现。run A+ #1/#2 证明 **动态判定 + 工作区证据在真实部署下成立**,且 `satisfied` 判定与 verifier 一致(reward=1)。

> 观察(非阻塞):run A+ #2 中 Kimi 给 milestone 3 的 JSON 加了一条 ground-truth 之外的要求("每个对象加 filename 字段")。本任务 scorer 只查数组长度不查条目键,故 reward 不受影响;属 §6.4 已知的"自然指令 vs ground-truth 一致性"风险,后续任务需留意。

## 6. 踩坑记录(重要)

1. **agent 认证**:沙箱 claude 必须有自己的 LLM 后端(host OAuth 不继承)。→ `.env` 加 `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`(DeepSeek 代理)。缺失时 claude `Not logged in` → trial error → verifier 全 0。
2. **LiteLLM 模型前缀**:`USER_LLM_MODEL` 必须带已知 provider 前缀(`openai/...`);裸 `moonshotai/kimi-k3` 使 LiteLLM 解析崩溃。
3. **Novita 余额**:LLM/沙箱都按账号计费;没余额 → 403 NOT_ENOUGH_BALANCE。GET /models、GET /templates 免费。
4. **ground-truth 与自然指令一致性**(本轮关键教训):scorer 必须与"user-LLM 转述后的自然消息能合理推导出的行为"一致。run #2 中 round_2 的 JSON 容器格式(数组 vs 对象)没写进 requirement/user_intent,agent 选扁平对象而被 scorer(要求 list-of-one)判 0。**修法**:把格式细节写进 `requirement` **和** `user_intent`(§4)。
5. **`.env` 安全**:真实 key 在 `.env`,已 git-ignore;`.env.example` 是模板。

## 7. 未完成工作

- [x] **run #3** → round_1,2,3=1,reward=1(user-LLM 转述质量已确认良好)
- [x] **Design A+(动态里程碑 + 纠正轮)**:新 schema(`milestones`/`max_rounds`/`max_corrections`)、`TurnController` 状态机、`judge_and_speak` 判定、scorer 遍历 milestones、测试 36/36(需求 1/2 落地;需求 3 更全面能力考察留待更复杂任务)
- [x] **工作区证据注入判定(run A+ #2 端到端验证)**:每轮 `/workspace` 快照 diff → `workspace_evidence` 注入判定 prompt,`satisfied` 基于 agent 实际改动而非自述;`decisions[]` 记录证据可审计。end-to-end reward=1
- [x] **user-LLM 判定质量**:run A+ #1/#2 在 Novita 上验证,`satisfied` 判定与 verifier 一致(全 1);观察到一个发散点:user-LLM 会加 ground-truth 之外的要求(见 §5 观察),在更复杂任务上继续观察
- [ ] **user-LLM 转述质量**:run #3 / A+ #2 通过,但只在 Kimi/K3 + 这一任务上验证过;更多任务/轮次上继续观察,必要时调 `prompt_templates.py`/persona
- [x] **提交所有改动** → `c1f5c41`(.gitignore、CLAUDE.md、README.md、pyproject.toml、uv.lock、scenario.json、.env.example、PROJECT_STATE.md;`.env` 已 git-ignore 不提交)
- [x] **真实端到端验证"只完成最后一轮"判别器**:`benchmark/last_only_agent.py`(确定性"只做最后里程碑"agent)→ Novita run(03:40)**round_1=0,round_2=0,round_3=1 → reward=0**,判别器在真实部署下有效
- [ ] 待用户补充的真实 benchmark 任务内容(当前只有 demo 任务)
- [x] **Design B(原生 multi-step)实现 + Novita 端到端验证**:`benchmark/{step_driver,multi_step_trial,interactive_step_agent,design_b_plugin}.py` + 多步任务 `tasks/benchmark/multi-round-cli-demo-multistep/`(6 预建步、共享根 tests、`multi_step_reward_strategy="final"`、无 min_reward)。单测 46/46(A+ 36 + StepDriver 6 + step-agent 4);Novita run(04:17)**reward=1**,per-step 稠密诊断 + 提前终止 + 每步轨迹全验证。**纯增量、可回退**(A+ 零改动)。可选后续:LastOnlyClaude 跑多步任务验证 reward=0

## 8. 未来计划(含 A+ 后改进)

**短期(当前轮)**:Design A+ 已单测覆盖 + Novita 端到端验证(reward=1,判定质量良好);判别器已端到端验证(reward=0);**下一步:Design B 在 Novita 上端到端跑通**(验证插件注入 → trial 子类 → 步间 runner → per-step reward 整条链),并检查 per-step reward.json/轨迹;之后可做判别器变体(LastOnlyClaude 跑多步任务)。

**Design B(原生 multi-step,已实现、e2e 待验证)**:每步 = 一轮,`InteractiveMultiStepTrial` 步间调 `StepDriver`(user-LLM 判定满意/纠正/强制推进),经包装的 `Task.step_instruction` 注入下一步指令(不改仓库)。**设计决策**:`multi_step_reward_strategy="final"`(最后一步全里程碑乘积,与 A+ 判别语义一致,避免 mean 稀释);**`min_reward` 不设**(与"纠正轮"冲突——纠正轮在某步天然低分,门控会提前中止;终止由 `TurnController` 控制);物理步数 = `max_rounds` 预建、有效轮数动态(`_run` 提前 break 避免尾随 no-op)。**注入路径**:`harbor run --plugin benchmark.design_b_plugin:DesignBPlugin`,`on_job_start` 在 `Trial.create` 前 monkeypatch `MultiStepTrial`(`Trial.create` 用函数体内局部 import,monkeypatch 有效)。

**Benchmark 质量改进方向**:
- user-LLM 判定保真(已完成并端到端验证):每轮把 /workspace 真实文件 diff 注入判定 prompt(`workspace_evidence`),`satisfied` 基于 agent 实际改动而非自述;`decisions` 日志记录证据,可审计。已在 `build_turn_decision_prompt` 加忠实度护栏(规则 5:不凭空添加 ground truth 之外的新字段/新格式/新硬性约束),抑制 §5 观察到的发散。
- user-LLM 忠实度:评估并提升 user-LLM 把 ground-truth(尤其格式/约束细节)转述进自然消息的可靠性;可能加"约束完整性"校验。
- ground-truth 设计规范:scorer 检查的每一点都应能从自然指令合理推出;避免"读心"式测试点。
- 更多任务/轮次;评估"自然、连续、必要"的介入质量。
- RLVR 落地:reward.json 多键 → VerifierResult.rewards → 训练信号。

## 9. 常用命令

```bash
H=.venv/bin/harbor; PY=.venv/bin/python
$PY -m pytest tests/                                   # 单元测试(无 Docker)
$H run -e novita --env-file .env -p tasks/benchmark/multi-round-cli-demo \
    -a benchmark.interactive_agent:InteractiveUserClaude -m deepseek-v4-flash   # 端到端
$H run -p <task> -e novita --print-config              # 零成本配置预检
# 本地验证参考解法(无容器):解 solve.sh 到临时 ws,再跑 scorer
#   sed 's|/workspace|<tmpws>|g' tasks/.../solution/solve.sh | bash
#   $PY tasks/.../tests/scorer.py --base-dir <tmpws> --scenario <scenario> --reward-out <out>
```

## 10. 相关文件索引

- 章程/框架参考:`CLAUDE.md`
- 用户手册:`README.md`
- 运行凭证模板:`.env.example`(真实 `.env` 已 git-ignore)
- 框架代码:`benchmark/`;示例任务:`tasks/benchmark/multi-round-cli-demo/`;单测:`tests/`
- 端到端 job 结果:`jobs/<timestamp>/`(result.json / agent/ / verifier/)
