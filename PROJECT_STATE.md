# PROJECT_STATE.md — 项目进展与交接文档

> 本文件是**新会话的第一入口**。先读 `CLAUDE.md`(章程/框架参考),再读本文件(当前进展/计划/踩坑)。更新日期:2026-08-11。

## 1. 项目目标与核心设计

在 [Harbor](https://github.com/laude-institute/harbor) 0.20.0 上构建**编程领域 Multi-turn 交互式 Agent Benchmark**。

与普通单轮任务的区别:**"用户"在 agent 完成任务过程中多次介入**,由一个独立 LLM(**user-LLM**)根据 agent 上一轮的实际输出,生成下一轮自然的 user 消息。评测 agent 是否:持续理解上下文、正确更新需求、不遗忘旧约束(回归)、最终完成编程任务。reward 可用于 RLVR。

**每轮 instruction 只包含模拟用户那一条自然消息(不重放历史)** —— agent 必须从环境(已有代码)+ 新消息重建上下文。

**介入是动态的(Design A+)**:**user-LLM 每轮返回 `{"satisfied": bool, "message": str}` 判定上轮输出是否满足当前里程碑** —— 满意→推进下一里程碑(message=下一需求);不满意→纠正轮(留在当前,≤ `max_corrections`);纠正耗尽→强制推进(该里程碑由 verifier 判 0)。总轮次由 `max_rounds` 硬上限约束,不再预写固定。

**核心判别(需求 #5)**:`reward = 各轮得分乘积`(稀疏 0/1)。只完成最后一轮的 agent → 早期轮 = 0 → reward=0;完整多轮的 agent → reward=1。

**reward 协议(与需求原文的偏差)**:需求写的是 `/logs/verifier/rewards.txt`,但 Harbor 0.20.0 verifier 只读 `reward.txt`(标量)或 `reward.json`(扁平 dict str→number)。本项目用 **`/logs/verifier/reward.json`**:`{"round_1":0|1, ..., "reward":<乘积>}`。多键 reward 是产品,documentation 里记录的 `rewards.json`/`rewards.txt` 均不存在,勿用。

**reward 双模式(2026-08-12 新增,devteam scorer 已实现)**:scorer 读 `REWARD_MODE` 环境变量(verifier 侧,`harbor run --ve REWARD_MODE=...` 传入),默认 **`dense`**:
- `dense`(默认):每轮 check 返回**连续 0-1**(子检查通过比例,如判别器 M3=0.3/M4=0.46),`reward`=逐轮分数乘积 → 有部分分,对 RLVR 更友好;
- `binary`(旧):每轮 0/1,`reward`∈{0,1} → 与历史结果完全一致(判别器仍 1,1,0,0→0)。
参考解在两种模式下都全 1。`run_model_compare.sh` 用 `REWARD_MODE` 透传(`--ve REWARD_MODE=$REWARD_MODE`,默认 dense);kimi 重试用 binary 复现旧语义。

## 2. 当前架构(双路径)

**Design A+**(本节,自定义 agent,单 trial,动态里程碑)—— 主实现,全部功能/验证完备;**Design B**(原生 multi-step,每步一轮 + 步间 runner,实验性、纯增量可回退)—— 见 §8 与 `tasks/benchmark/multi-round-cli-demo-multistep/`。

```
harbor run -e novita --env-file .env -p <task> -a benchmark.interactive_agent:InteractiveUserClaude -m <model>
  │ agent.run() [宿主进程,PYTHONPATH=. 导入 benchmark/]
  │   读容器内 scenario.json(exec cat /scenario.json)
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
- 文件布局:`benchmark/{interactive_agent,controller,user_simulator,scenario,prompt_templates,last_only_agent}.py`;`tasks/benchmark/multi-round-cli-demo/`;`tests/`(无 Docker 单元测试,含 controller/scorer)。
- **Design B 并行路径**:`benchmark/{step_driver,multi_step_trial,interactive_step_agent,design_b_plugin}.py` + `tasks/benchmark/multi-round-cli-demo-multistep/`,运行命令见 §9。纯增量,回退 = 删增量文件 + 任务目录。

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

3 轮累计场景(stats CLI):基础摘要 → `--output-json`(明确要求**总是数组**)→ 多文件(保留 1、2 轮功能)。**Design B 变体**:`tasks/benchmark/multi-round-cli-demo-multistep/`(同一 scenario.json/Dockerfile/scorer,多步结构:`[[steps]]` step-1..6、共享根 `tests/`、`multi_step_reward_strategy="final"`、无 `min_reward`)。

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

**scenario.json(environment/,会烘焙进容器 /scenario.json(不在 /workspace,防 agent 偷看未来里程碑——见 §6.12);harness 与 verifier 读它;Design A+ 新 schema):**
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

### 4.1 新增任务套件(T1–T3,2026-08-11)

在 stats 基线之上新增 3 个任务,构成复杂度阶梯,考察不同能力维度。设计依据见 `docs/task-suite-design.md`(文献调研:SWE-Interact / SWE-Together / τ-bench / EvoCode-Bench 等;能力分类 + 累计里程碑设计教训 + user-LLM 可靠性对策)。

| 任务 | 目录 | 轮次 | 工作区起点 | 考察重点 |
|---|---|---|---|---|
| T1 todo-tracker | `tasks/benchmark/todo-tracker/` | 4 | 空 | 状态持久化(todos.json)、数据建模、过滤/统计/搜索、跨进程一致性、回归 |
| T2 repofix | `tasks/benchmark/repofix/` | 3 | **预置坏仓库 + 失败测试**(seed/ 烤进镜像) | 调试、边缘用例、自我验证(pytest)、重构(≥3 函数)、回归测试 |
| T3 pkg-wordcount | `tasks/benchmark/pkg-wordcount/` | 3 | 空 | 真实生态(pip install -e + console 入口 + pytest)、包结构、API 设计、回归 |
| T4 support-bot | `tasks/benchmark/support-bot/` | 4 | **预置知识库 + 订单 API mock + docs**(seed/ 烤进镜像;ground_truth/ 只给 verifier) | 客服机器人:知识库问答 → 订单 API 集成 → 包重构 + 批量 → 多语言 + 转人工;考察**长上下文**(复杂真实应用)、**主动澄清**(agent 提问 → user 回答)、**需求变更/大幅重构**、**长期记忆 + 遗忘被推翻规则**(语言规则翻转) |
| T5 ticket-system | `tasks/benchmark/ticket-system/` | 4 | **预置业务文档 + 接口契约 + 样例导出**(seed/ 烤进镜像;ground_truth/ 只给 verifier) | **产品开发类**(内部工单系统 HTTP 服务):CRUD → 工作流/搜索/筛选 → 包重构+SQLite+SLA → **软删除反转**+统计;verifier **起真实 HTTP 服务器**(临时端口+临时 DB)做端到端检查、重启持久化、SQLite 魔数、隐藏输入 |
| T6 devteam | `tasks/benchmark/devteam/` | 4 | 空 | **协同开发工具**(CLI + HTML 仪表盘):项目/成员/角色权限 → 迷你 VCS + 协作署名 → 日程+UI(status/`--output-json`/dashboard 自包含 HTML) → 质量检查+自动补全+**viewer 权限反转**;考察**长上下文**(复杂真实应用)、**主动澄清**(terse brief → agent 提问 → user 据 user_knowledge 回答)、**需求变更**、**长期记忆 + 遗忘被推翻规则**(权限反转双轴);配套**真人扮演用户模式**(`USER_SIMULATOR=manual`) |

- **每任务均本地验证**:参考解法 → 全轮 1 / reward=1;判别器(只做部分轮次)→ reward=0(§5)。T3 判别器演示了 **round_1=1, round_2=0, round_3=1 → reward=0**(跳过中间里程碑也被乘积捕获)。
- **ground-truth/user_intent 一致性**(§6.4 教训)已贯彻:格式/约束细节(如 todos.json 位置、JSON 永远数组、console 入口名、pipeline 分组列)同时写进 `requirement` 与 `user_intent`。
- **verifier 防作弊**:T2/T3 用隐藏输入 + 隐藏行为检查,防"建到可见测试";T2 对"改写可见测试"的抗性已验证(§5)。
- **T3 本地验证需 pip**:venv 已 `ensurepip` 装好 pip(uv 创建的 venv 默认无 pip);scorer 的 `pip install -e` 有 `--user` 回退,console 脚本解析有 `sys.executable` 同目录回退(容器内 python3-pip 装到 /usr/local/bin,天然在 PATH 上)。

## 5. 验证结果(截至 2026-08-11)

| 验证 | 结果 |
|---|---|
| 单元测试 `.venv/bin/python -m pytest tests/`(Design A) | ✅ 12/12 |
| 单元测试(Design A+ + 证据,含 controller/scorer/parse) | ✅ 36/36 |
| 单元测试(Design B 增量:StepDriver 6 + step-agent 4) | ✅ 46/46 总计(A+ 36 + B 10) |
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
| 端到端 Design B 判别器(04:48,`LastOnlyClaude`) | ✅ **round_1=0,round_2=0,round_3=1 → reward=0**。多步链路判别器有效;**纠正轮真实触发**(decisions:每里程碑 1 次纠正 + 1 次强制推进);5/6 步后 round_count 到顶提前终止 |
| T1 todo-tracker(本地,无容器) | ✅ 参考解法 → **round_1..4=1,reward=1**;只做最后轮实现 → **reward=0**;scorer 单测 8/8 |
| T2 repofix(本地,无容器) | ✅ 参考解法 → **round_1,2,3=1,reward=1**;**未修复的种子仓库 → 全 0**;**只做 R3 判别器(改可见测试作弊)→ 全 0(隐藏检查兜住)**;scorer 单测 7/7 |
| T3 pkg-wordcount(本地,无容器) | ✅ 参考解法 → **round_1,2,3=1,reward=1**;**判别器(跳过 top_words/R2)→ round_1=1, round_2=0, round_3=1,reward=0**(乘积捕获"跳过中间里程碑");scorer 单测 5/5 |
| 3 个新任务 Harbor 预检 | ✅ `harbor run -p <task> -e novita --print-config` 全部通过;scenario.json 经 `Scenario` 模型解析 OK |
| 澄清子循环(框架新能力)单测 | ✅ 82 项(A+ 36 + B 10 + todo 8 + repofix 7 + pkg 5 + 澄清 16);含 stay-on-milestone / 预算耗尽→纠正 / 强制推进重置 / 解析 action |
| T4 support-bot(本地,无容器) | ✅ 参考解法 → **round_1..4=1,reward=1**;部分实现判别器(只做 KB+订单,停在中途)→ **round_1,2=1,round_3,4=0,reward=0**;Harbor 预检 OK;scenario 经 `Scenario` 模型解析 OK;scorer 单测 10/10 |
| T5 ticket-system(本地,无容器) | ✅ 参考解法 → **round_1..4=1,reward=1**;部分实现判别器(只做 CRUD+工作流,单脚本 JSON,无包/SQLite/SLA/软删除/统计)→ **round_1,2=1,round_3,4=0,reward=0**;Harbor 预检 OK;scenario 经 `Scenario` 模型解析 OK;scorer 单测 11/11 |
| T6 devteam(本地,无容器) | ✅ 参考解法 → **round_1..4=1,reward=1**;判别器(仅 M1+M2,viewer 仍只读、无 M3/M4)→ **round_1=1,round_2=1,round_3=0,round_4=0,reward=0**;Harbor 预检 OK;scenario 经 `Scenario` 模型解析 OK;devteam scorer 单测 6/6 + manual_user 7/7 |
| 单测总计 | ✅ **116/116**(框架 89 含 manual_user 7 + support-bot scorer 10 + ticket-system scorer 11 + devteam scorer 6) |
| 端到端 todo-tracker #1(07:26) | ⚠️ **round_1..4=0,reward=0** —— 交互链路完美(4 轮全 satisfied、agent 实现全部正确),但 verifier 因 **scorer 候选入口 bug** 全判 0(§6.9)。已修 scorer |
| 端到端 todo-tracker #2(07:50,已修 scorer) | ✅ **round_1,2,3,4=1,reward=1**。首个非 demo 任务的完整 e2e:4 轮全 satisfied、无纠正轮;user-LLM 消息忠实(引用实际行为:JSON 数组、[done] 后缀、priority 默认、report 三行含 0、search 大小写不敏感)且 **判定与 verifier 完全一致**(judge-vs-scorer 分歧=0) |
| 端到端 devteam #1(15:31) | ❌ **AgentTimeoutError,无 reward** —— 交互正常推进到第 6 轮(M4 进行中),撞上 **Novita 沙箱 1h 自动销毁**(`_SANDBOX_TIMEOUT_SEC=3600`),agent 读 `/logs/agent/claude-code.txt` 时沙箱已删 → 整轮 errored;transcript 未同步(§6.10) |
| 端到端 devteam #2(16:36,长沙箱插件) | ✅ **round_1,2,3,4=1,reward=1**,0 异常,54m 9s。`--plugin benchmark.debug_long_sandbox_plugin:LongSandboxPlugin` 把沙箱上限提到 2h。5 轮 agent + 1 次澄清(M4),judge-vs-scorer 分歧=0。观察:①**user 反馈真实且锚定实际行为**("权限拦截都好使/回滚完文件内容对/测试 38 条全绿";M4 澄清精确划定反转范围:commit/rollback 全员、event 维持、check 只扫 .py);②**agent 在引导下完成全部里程碑**:M1 轮超量交付(M1+M2+21 测试),M4 主动反问(澄清子循环)后正确执行 **viewer 权限反转**(commit/rollback 改全员 = 遗忘旧规则)且 M1–M3 全保留(记忆);③**交流轮充足**:M1=1/M2=1/M3=1/M4=2,5/12 轮,无 force-advance;但每轮 ~8-9 分钟 → 1h 沙箱只够 ~6 轮,`max_rounds=12` 实际不可达,需要长沙箱或压紧任务 |
| devteam 难度硬化(2026-08-12) | 🔧 **最小可用模型 flash 在旧 scorer 下 reward=1 → 任务对"模型对比"分辨率不足**(但判别器仍把"只做部分里程碑"判 0)。已硬化 scorer + 修参考解 + 补 scenario 权限表述:①M4 反转**作用域收紧**——viewer 能 commit/rollback 但 **event add/remove 仍仅 owner/member**(旧参考解漏了 viewer 管日程的拦截,已加 `require_editor`);②`check` **精度**——干净文件零输出、字符串里的 `"TODO"` 不得误报(参考解改 `tokenize` 只认 COMMENT 令牌)、未定义变量需真 AST;③`status` 断言**精确计数**;④边界用例——空项目提交、回滚不存在提交/移除不存在成员/日程报错、非成员读写全拦、unicode+嵌套文件名。本地验证:参考解仍 reward=1、判别器仍 0、**"viewer 可管日程"的偷懒实现 → round_4=0**(旧 scorer 会给 1,证明硬化有效) |
| 端到端 devteam #3(00:25,硬化后 scorer + 新 prompt 规则) | ✅ 完成、0 异常、22m53s(插件稳定:长沙箱跑完 + transcript 同步)。**硬化生效、直接回答"是否太简单"**:最小模型 flash **不再全 1**——`round_1,2,3=1,round_4=0,reward=0`。M4 反转被 agent **过度泛化**("只读概念彻底没了"):event add/remove 仍走 `require_member`(viewer 可管日程,会话代码里 `require_editor` 计数 0),被硬化 M4 检查"viewer 不得管理日程"判 0。**judge-vs-scorer 分歧=1**:user 判 M4 satisfied(只验了 check 格式/autocomplete/viewer 提交,没测 viewer-event 限制),verifier 判 0——正是 §2.4 的哨兵场景,实证"user 判定是对话控制信号、verifier 才是最终裁判"。**结论**:硬化后任务对"需求作用域把握"有区分度 |
| 端到端 devteam grid #1(00:51,deepseek-v4-flash,硬化后) | ✅ 完成、0 异常、25m37s。**round_1,2,3=1,round_4=0,reward=0**(与 run #3 同结果但**根因不同**):这次是 `check` **误报字符串里的 TODO**——agent 把 `msg = "# TODO: not a marker"` 也报了 `todo_string.py:1: 发现 TODO 标记`,被硬化精度检查(只认 COMMENT 令牌、字符串不误报)判 0;check 的语法/未定义变量、autocomplete、viewer 提交、日程拦截全对。**user 建议功能验证**:user 给了一条体验建议("history 能带出每次提交动了哪些文件")且 agent 采纳实现(M2 轮即落地)。judge-vs-scorer 分歧=1。**结论**:flash 两次独立运行以不同方式栽在 M4(一次作用域过度泛化、一次 check 精度),硬化区分度成立 |
| 端到端 devteam grid #2(01:23,deepseek-v4-pro,硬化后) | ✅ 完成、0 异常、22m27s。**round_1,2=1,round_3=0,round_4=0,reward=0**——pro **不如 flash**:M3 也挂了(6 轮、2 纠正、0 澄清、**judge-vs-scorer 分歧=3**)。M3 根因(已用会话重建最终代码复现):status 输出 `文件数: 2`(≠要求钉死的 `代码文件数: 2`;user 明说"别自己造别的写法")+ dashboard 写 `carol_dashboard.html`(≠要求的 `dashboard-<项目名>.html`,该名在 requirement 与 user_knowledge 里、**澄清可问出,但 pro 全程 0 澄清**)。M4 根因:check **漏报语法错误与未定义变量**(只做了 TODO 且字符串误报),而 user 判定 M4 satisfied 自称"三种都抓到了"——**user 判定失实**。**结论**:两个真实模型都以真实缺陷 reward=0,参考解 1;pro 全程不澄清 → 恰好撞上"主动澄清"能力轴 |
| devteam 多API grid(2026-08-12,4 后端) | ✅ 4 模型全部完成:zai/glm-5.2 **reward=1**(3 澄清、分歧 0);deepseek-flash reward=0(R4 挂);aliyun/qwen3.5-flash reward=0(R2-R4 挂、1 force-advance);moonshot/kimi-k3 reward=0(R1=1,R2-4=0,见下条)。**结论**:硬化后任务区分度成立,区分轴≈"细节拿不准是否主动澄清";多 API 打通,踩坑 **`--env-file` 是 `load_dotenv(override=True)`,会覆盖命令行设的 `ANTHROPIC_BASE_URL` → agent 一直打 DeepSeek**;修复=每 backend 生成临时 env(剥掉 .env 的 `ANTHROPIC_*` 换成该 backend),模型 id 用各端点 Anthropic 原生的(不带 `[1m]`/OpenAI 目录前缀) |
| 端到端 devteam kimi-k3 重跑(07:33,binary reward,超时修复后) | ✅ 完成、**62 分钟无超时**(request_timeout 修复验证成功:旧 30 分钟单命令上限不再误杀慢轮)。**reward=0,R1=1,R2-4=0**——kimi 最弱:仅 M1 过;M2 起**不问就猜**:命令格式猜错(`--project` vs 位置参数)、rollback 语义猜错(生成新提交而非恢复文件)。3 轮、1 澄清、1 纠正、分歧 1。**grid 至此全部完成**:glm=1,flash/qwen/kimi=0 |

run #2 证明整条流水线(Design A + Novita + DeepSeek 后端 + Kimi user-LLM)在真实部署下工作;round_2=0 不是 harness bug,是任务规格问题。run #3 证明 **ground-truth 显式化 → user-LLM 忠实转述 → agent 正确实现**的链路成立:把格式/约束细节写进 `requirement`+`user_intent`,Kimi 会原样传达(甚至补充理由),agent 据此实现。run A+ #1/#2 证明 **动态判定 + 工作区证据在真实部署下成立**,且 `satisfied` 判定与 verifier 一致(reward=1)。devteam #2 进一步证明:**复杂 4 里程碑任务 + 权限反转**在真实部署下 judge-vs-scorer 完全一致;user-LLM 在 M4 澄清轮给出的范围约束(只放开 commit/rollback、event 维持、check 收 .py)正是 verifier 所查的边界。

> 观察(非阻塞):run A+ #2 中 Kimi 给 milestone 3 的 JSON 加了一条 ground-truth 之外的要求("每个对象加 filename 字段")。本任务 scorer 只查数组长度不查条目键,故 reward 不受影响;属 §6.4 已知的"自然指令 vs ground-truth 一致性"风险,后续任务需留意。

## 6. 踩坑记录(重要)

1. **agent 认证**:沙箱 claude 必须有自己的 LLM 后端(host OAuth 不继承)。→ `.env` 加 `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`(DeepSeek 代理)。缺失时 claude `Not logged in` → trial error → verifier 全 0。
2. **LiteLLM 模型前缀**:`USER_LLM_MODEL` 必须带已知 provider 前缀(`openai/...`);裸 `moonshotai/kimi-k3` 使 LiteLLM 解析崩溃。
3. **Novita 余额**:LLM/沙箱都按账号计费 → 403 NOT_ENOUGH_BALANCE。GET /models、GET /templates 免费。
4. **ground-truth 与自然指令一致性**(本轮关键教训):scorer 必须与"user-LLM 转述后的自然消息能合理推导出的行为"一致。run #2 中 round_2 的 JSON 容器格式(数组 vs 对象)没写进 requirement/user_intent,agent 选扁平对象而被 scorer(要求 list-of-one)判 0。**修法**:把格式细节写进 `requirement` **和** `user_intent`(§4)。
5. **`.env` 安全**:真实 key 在 `.env`,已 git-ignore;`.env.example` 是模板。
6. **Design B 本地任务目录就地使用**:`-p` 本地任务 Harbor **不复制**,直接在用户源码目录上跑;运行时改写 `steps/*/instruction.md` 会污染仓库。→ Design B 用**包装 `Task.step_instruction`** 注入生成指令(每次现读、无缓存),不写文件。
7. **`min_reward` 与纠正轮冲突**:纠正轮在某步天然低分,`min_reward` 门控会提前中止、破坏纠正语义。→ Design B **不设 `min_reward`**,终止由 `TurnController` 控制。
8. **AGENT_END 钩子时机**:非 mounted 环境在 `AGENT_END` 时当前步 agent 输出**尚未下载到宿主**(`_sync_agent_output` 在步末才执行)。→ 可靠读点在**归档后**(自定义 trial 的 `_after_step`,`_archive_step_outputs` 同步执行)或 `VERIFICATION_START`。
9. **scorer 候选入口发现必须跳过"文件不存在"**:todo-tracker e2e(07:26)agent 把实现写在 `/workspace/todo.py`(合法),但 scorer 候选 1 `python3 src/todo.py` 在**文件缺失时是 rc=2 的 CompletedProcess**(python 报 "can't open file"),不是 FileNotFoundError → `run_todo` 把 rc=2 当结果返回、短路了真正的 `/workspace/todo.py` → verifier 全 0(而 user-LLM 判定全 satisfied,agent 实现本身完全正确)。demo 的 `run_stats` 靠 `rc==0 and stdout` 门槛躲过;todo scorer 改为**先 `os.path.exists(cmd[-1])` 跳过不存在入口**。**教训**:多候选入口的 scorer 必须显式跳过缺失文件,或要求 rc==0+非空 stdout 才接受候选。
10. **Novita 沙箱 1h 自动销毁 + 可观测性**(devteam e2e,2026-08-11):沙箱寿命是安装包硬编码 `NovitaEnvironment._SANDBOX_TIMEOUT_SEC = 3600`(novita.py:693),在 `_create_sandbox` 时作 `timeout=` 传给 novita_sandbox SDK;**task.toml `[environment]` 没有沙箱寿命字段**(只有 `build_timeout_sec` 模板构建),也不读环境变量。devteam #1 在第 6 轮撞上限 → 沙箱被删 → agent 的 `_read_agent_output` exec 抛 `TimeoutException: The sandbox was not found` → 整 trial errored,**无 verifier、transcript 未同步**。**对策**:①观察跑用 `--plugin benchmark.debug_long_sandbox_plugin:LongSandboxPlugin`(`on_job_start` 改类属性;值取 `NOVITA_SANDBOX_TIMEOUT`,默认 2h;计费按实际时长不是上限);②`interactive_agent.py` 增加**每轮 `[decision]` 日志 + 增量写 transcript**,半途死也能复盘;③**harbor CLI 进程要能 import `benchmark`**:`PYTHONPATH=/ssd/xueshenye/proj`(缺了报 `No module named 'benchmark'`,trial 起不来)。**教训**:每轮 ~8-9 分钟(claude --print + 快照 + user-LLM)下,1h 沙箱只够 ~6 轮,复杂任务要么压紧轮次、要么提供可配置沙箱寿命。
11. **novita_sandbox 单命令 30 分钟请求超时(慢模型误杀)**:kimi-k3 在 M2 的 claude 轮流式输出 >30 分钟被掐(`Execution timed out — the 'timeout' option can be used to increase this timeout`,来自 `novita_sandbox command.run` 的 `request_timeout`)。机制:Harbor `_run_command` 传 `timeout=timeout_sec or 0`(0=命令连接无限,SDK 语义),但**不传 `request_timeout`** → SDK `ConnectionConfig` 默认 `LEGACY_REQUEST_TIMEOUT=1800`(30 分钟),对流式命令请求封顶 → 慢模型单轮被误杀。**修复**:①venv 补丁 `novita.py:1394 _run_command` 的 `commands.run(...)` 加 `request_timeout=0`(SDK `_get_request_timeout(0)=None` = 无限;**uv sync 会冲掉,需重打**);②`LongSandboxPlugin` 加 runtime monkeypatch(`ConnectionConfig.get_request_timeout → None`,插件跑就生效,不依赖包补丁);总时长仍受 trial/agent 超时 + 沙箱寿命(插件 2h)约束。**已验证**:kimi 重跑(07:33)62 分钟无超时跑完,修复端到端有效(旧 30 分钟单命令上限不再误杀慢轮;之前 error 的那轮 M2 正常执行)。
12. **scenario.json 泄漏给 agent(导致 M1 预知 M4)**:scenario.json 原被 `COPY` 进 `/workspace`,而 agent 的工作目录就是 /workspace → 它列目录就看到并 `Read` 了它(实测多轮运行都出现 `Read: /workspace/scenario.json`),从而**在一开始就知道全部 4 个里程碑(含 M4 的 viewer 反转)**。后果:①"跳跃/超前实现"(M1 就做 viewer-commit、提前做 M2 的 VCS)大部分是泄漏产物,不是模型真实行为;②破坏"渐进揭示"核心设计(本应每轮只从用户消息学一个里程碑);③此前所有端到端/grid 结果都被此污染。**修复**:scenario.json 移到 `/scenario.json`(不在 /workspace),`Dockerfile`/`interactive_agent.py`/`scorer.py` 三处路径同步改;harness 与 verifier 照常读,agent 不再自然可见。**教训**:ground truth 必须放 agent 工作目录之外;最终 clean grid 基于此重跑。

- [x] **run #3** → round_1,2,3=1,reward=1(user-LLM 转述质量已确认良好)
- [x] **Design A+(动态里程碑 + 纠正轮)**:新 schema(`milestones`/`max_rounds`/`max_corrections`)、`TurnController` 状态机、`judge_and_speak` 判定、scorer 遍历 milestones、测试 36/36(需求 1/2 落地;需求 3 更全面能力考察留待更复杂任务)
- [x] **工作区证据注入判定(run A+ #2 端到端验证)**:每轮 `/workspace` 快照 diff → `workspace_evidence` 注入判定 prompt,`satisfied` 基于 agent 实际改动而非自述;`decisions[]` 记录证据可审计。end-to-end reward=1
- [x] **user-LLM 判定质量**:run A+ #1/#2 在 Novita 上验证,`satisfied` 判定与 verifier 一致(全 1);观察到一个发散点:user-LLM 会加 ground-truth 之外的要求(见 §5 观察),在更复杂任务上继续观察
- [ ] **user-LLM 转述质量**:run #3 / A+ #2 通过,但只在 Kimi/K3 + 这一任务上验证过;更多任务/轮次上继续观察,必要时调 `prompt_templates.py`/persona
- [x] **提交历史**:`c1f5c41`(MVP,Design A)→ `048434f`(Design A+ + 工作区证据)→ `c40b111`(忠实度护栏 + 判别器 agent)→ `4ca768e`(Design B,原生 multi-step)→ `4fe1eb8`(Design B 判别器验证记录)。`.env` 已 git-ignore 不提交;Design B 独立 commit 便于回退
- [x] **真实端到端验证"只完成最后一轮"判别器**:`benchmark/last_only_agent.py`(确定性"只做最后里程碑"agent)→ Novita run(03:40)**round_1=0,round_2=0,round_3=1 → reward=0**,判别器在真实部署下有效
- [x] **Design B(原生 multi-step)实现 + Novita 端到端验证**:`benchmark/{step_driver,multi_step_trial,interactive_step_agent,design_b_plugin}.py` + 多步任务 `tasks/benchmark/multi-round-cli-demo-multistep/`(6 预建步、共享根 tests、`multi_step_reward_strategy="final"`、无 min_reward)。单测 46/46(A+ 36 + StepDriver 6 + step-agent 4);Novita run(04:17)**reward=1**(提前终止、per-step 稠密诊断、每步轨迹);判别器 run(04:48,LastOnlyClaude)**reward=0** + 纠正轮真实触发。**纯增量、可回退**(A+ 零改动)
- [x] **任务套件 T1–T3(文献调研 + 实现 + 本地验证)**:`docs/task-suite-design.md`(调研 SWE-Interact/SWE-Together/τ-bench/EvoCode-Bench 等 → 能力分类 → 任务规格);实现 `tasks/benchmark/{todo-tracker,repofix,pkg-wordcount}/`(scenario+instruction+Dockerfile+scorer+solve.sh+test.sh)。本地验证:全参考解 → reward=1,判别器 → reward=0,单测 66/66(新增 20),Harbor 预检通过。**端到端 Novita 运行待跑**(见 §8)
- [x] **澄清子循环(agent 提问 → user 回答)框架能力**:`Milestone.user_knowledge` / `Scenario.max_clarifications`;`TurnDecision.action`(judge/answer);`TurnController` 澄清分支(留在同里程碑、不消耗纠正、不推进;超预算降级为纠正);prompt 澄清规则 + user_knowledge 注入 + 软性"以客户身份实测"提示;`decisions[]` 记录 `action`;单测 66→82
- [x] **T4 support-bot(客服机器人,实现 + 本地验证)**:4 里程碑(知识库问答 → 订单 API → 包重构+批量 → 多语言+转人工);`seed/`(用户材料)+ `ground_truth/`(仅 verifier,含隐藏 `facts.json`);合成订单由 verifier 运行时生成(防硬编码/防篡改);参考解法 reward=1、部分实现判别器(`benchmark/partial_support_bot.py:FirstTwoClaude`,只做 KB+订单)→ reward=0
- [x] **T5 ticket-system(产品开发类任务,实现 + 本地验证)**:内部工单系统 HTTP 服务,4 里程碑(CRUD → 工作流/搜索/筛选 → 包重构+SQLite+SLA → **软删除反转**+统计);`api.md` 作为 **v1 契约把删除固定为永久**,软删除/恢复由 M4 引入 → 反转真实而非"补端点";verifier 起真实服务器(临时端口 + 临时 `TICKET_DB`)做端到端 HTTP 检查、重启持久化、SQLite 魔数、隐藏输入、docs 防篡改;参考解法 reward=1、判别器(`benchmark/partial_ticket_system.py:PartialTicketClaude`,只做 CRUD+工作流)→ reward=0。依据:两份调研(基准任务类型 + 真实 vibecoding 体验),设计为"零到一产品构建、真实用户面(HTTP)、真实数据层、需求反转、agent 必须跑起产品"
- [x] **T6 devteam(协同开发工具,实现 + 本地验证)**:把真实产品草稿 `task_1.txt` 落成 headless 可验证任务。4 里程碑(项目/成员/角色 → 迷你 VCS+协作署名 → 日程+UI:status/`--output-json`/dashboard 自包含 HTML → 质量检查+自动补全+**viewer 权限反转**);CLI + HTML 仪表盘,无 seed/ground_truth,测试输入在 scorer 内 seeded-RNG 即时生成;参考解 reward=1、判别器(`benchmark/partial_devteam.py:PartialDevteamClaude`,仅 M1+M2、viewer 仍只读)→ reward=0。**新增真人扮演用户模式**:`benchmark/manual_user.py`(`USER_SIMULATOR=manual` 换入,打印里程碑需求+评价标准+agent 输出,人手输 s/c/a 简写或严格 JSON 判定),任务作者可亲手跑 demo 给改进意见
- [x] **T6 devteam 端到端 Novita 运行**:见 §5。run #1(15:31)撞沙箱 1h 上限 errored(§6.10);run #2(16:36,长沙箱插件)**reward=1、judge-vs-scorer 分歧=0**——观察结论:user 反馈真实锚定实际行为、agent 在引导下完成全部里程碑含 M4 权限反转、交流轮 5/12 充足但每轮 ~8-9 分钟使 1h 沙箱只够 ~6 轮(需长沙箱或压紧任务)。真人扮演模式(`USER_SIMULATOR=manual`)仍可用于任务作者亲手走一遍
- [ ] **T5 端到端 Novita 运行**:跑 1-2 次,观察 M1 澄清轮、agent 开发期自己起服务、M4 删除反转的 user-LLM 转述与 verifier 评分是否一致(judge-vs-scorer 分歧)
- [ ] **T4 端到端 Novita 运行**:跑 1-2 次,观察澄清轮真实触发(`decisions[].action=="answer"`)、user-LLM 对 M4"语言跟随客户"规则翻转的转述质量、judge-vs-scorer 分歧

## 8. 未来计划(含 A+ 后改进)

## 8. 未来计划(含 A+ 后改进)

**短期(当前轮)**:Design A+ 与 Design B **均已实现并经 Novita 端到端验证**;任务套件 **T1–T6 已实现并本地验证**(todo-tracker / repofix / pkg-wordcount / support-bot / ticket-system / **devteam**,见 §4.1)。**新能力**:①澄清子循环(agent 主动提问 → user-LLM 据 `user_knowledge` 回答,不消耗纠正、不推进,超 `max_clarifications` 降级为纠正)——为 T4/T5/T6 这类"模糊起步、agent 必须澄清"的任务铺路;②**真人扮演用户模式**(`benchmark/manual_user.py`,`USER_SIMULATOR=manual` 换入:打印里程碑需求+评价标准+agent 输出+工作区 diff,人手输 s/c/a 简写或严格 JSON 判定),任务作者可不依赖 user-LLM 亲手跑 demo、读本地验收文档判断达标、给改进意见。**T5 ticket-system** 是按调研结论设计的**产品开发类任务**(内部工单系统 HTTP 服务:零到一构建 + 真实数据层 + 状态机工作流 + 需求反转 + verifier 起真实服务器端到端检查);**T6 devteam** 把真实产品草稿 `task_1.txt`(协同开发工具)落成 headless 可验证任务(CLI + HTML 仪表盘,4 里程碑,含权限反转 + 澄清)。**下一步**:为 T1–T6 各跑 1-2 次 Novita 端到端(需 `.env` 凭证,按量计费;命令见 §9),重点观察:
- **user-LLM 判定/转述质量在更复杂任务上是否稳定**(文献警示:τ²-bench 47% 对话含模拟器错误;"Lost in Simulation" ±9pp 用户模型敏感性)——建议跑完记录判定 vs verifier 分歧;
- repofix(T2)的**调试轮**是否真实触发纠正、pkg(T3)的 `pip install` 在沙箱内是否顺畅;
- 之后推动 RLVR 落地(reward.json 多键 → VerifierResult.rewards → 训练信号)。

**Design B(原生 multi-step,已实现并 e2e 验证)**:每步 = 一轮,`InteractiveMultiStepTrial` 步间调 `StepDriver`(user-LLM 判定满意/纠正/强制推进),经包装的 `Task.step_instruction` 注入下一步指令(不改仓库)。**设计决策**:`multi_step_reward_strategy="final"`(最后一步全里程碑乘积,与 A+ 判别语义一致,避免 mean 稀释);**`min_reward` 不设**(与"纠正轮"冲突——纠正轮在某步天然低分,门控会提前中止;终止由 `TurnController` 控制);物理步数 = `max_rounds` 预建、有效轮数动态(`_run` 提前 break 避免尾随 no-op)。**注入路径**:`harbor run --plugin benchmark.design_b_plugin:DesignBPlugin`,`on_job_start` 在 `Trial.create` 前 monkeypatch `MultiStepTrial`(`Trial.create` 用函数体内局部 import,monkeypatch 有效)。已验证:插件注入→trial 子类→步间 runner→per-step reward 整条链(§5);per-step 稠密诊断 + 每步轨迹保留。

**Benchmark 质量改进方向**:
- user-LLM 判定保真(已完成并端到端验证):每轮把 /workspace 真实文件 diff 注入判定 prompt(`workspace_evidence`),`satisfied` 基于 agent 实际改动而非自述;`decisions` 日志记录证据,可审计。已在 `build_turn_decision_prompt` 加忠实度护栏(规则 5:不凭空添加 ground truth 之外的新字段/新格式/新硬性约束),抑制 §5 观察到的发散。
- user-LLM 忠实度:评估并提升 user-LLM 把 ground-truth(尤其格式/约束细节)转述进自然消息的可靠性;可能加"约束完整性"校验。
- ground-truth 设计规范:scorer 检查的每一点都应能从自然指令合理推出;避免"读心"式测试点。
- 更多任务/轮次;评估"自然、连续、必要"的介入质量。
- RLVR 落地:reward.json 多键 → VerifierResult.rewards → 训练信号。

**多模型评估(2026-08-12 新增)**:`benchmark/run_model_compare.sh` —— 固定 user-LLM(.env 的 kimi-k3)、只变 **agent 模型**,逐模型输出 reward/逐轮/agent 轮数/澄清/纠正/force-advance/判分分歧/时长/费用。**关键发现**:①DeepSeek Anthropic 端点只有 `deepseek-v4-flash`/`deepseek-v4-pro` 两个 distinct agent 模型,**`claude-sonnet-5` 被静默别名成 flash**(探测证实,勿当第 3 个);②**多 API 已打通**:脚本支持 deepseek/zai(智谱 GLM)/moonshot(Kimi)/aliyun(Qwen)四个 Anthropic 兼容后端,base URL 与模型 id 从 .env 解析;**踩坑:harbor `--env-file` 是 `load_dotenv(override=True)`,会覆盖命令行设的 `ANTHROPIC_BASE_URL` → agent 一直打 DeepSeek、任何非 deepseek 模型吃 400**。修复=每 backend 生成临时 env(剥掉 .env 的 `ANTHROPIC_*` 换成该 backend)。③**首次 4 模型 grid 结果**:zai/glm-5.2 = **reward=1**(3 澄清、分歧=0,唯一通过);deepseek-flash = 0(R4 挂);aliyun/qwen3.5-flash = 0(R2-R4 挂、1 force-advance,最差);moonshot/kimi-k3 = **error**(慢轮触发 novita 单命令超时,有效 0)。**结论**:任务区分度成立,区分轴≈"细节拿不准是否主动澄清"——会澄清的 glm-5.2 一次没猜错、分歧 0;不问就猜的 flash/qwen 都栽;慢模型会被 sandbox 单命令超时误杀(需调大)。**user 体验增强(已生效)**:prompt_templates 规则 7 强化——user-LLM 要"真的上手跑过"agent 做的东西,并在合适时机**至少给一条**关于 UI/易用性、编码流畅度、功能完整度的改进建议(口语化期望,verifier 不查、不判 false);多次实测 user 给建议且 agent 采纳(history 带改动文件、member list 按角色排序等)。

**EvoCode-Bench 对标改进(2026-08-12)**:给 `run_model_compare.sh` 加 **MT@k 多轮采样评估**(默认 `MT@2`,任务级独立重跑,MT@k=达 reward=1 占比,适配 EvoCode-Bench 的 MT@4;每次 attempt 结果缓存支持续跑)+ **per-round 均值衰减曲线 + 首败轮次** + **reference/nop 基线行**(本地算,不跑 harbor)。未做(记不足):扩任务套件、轮级并行采样、任务侧加第 5 里程碑。

**最终 clean dense grid(2026-08-12,MT@2,scenario 泄漏修复后)**:4 模型 × 2 attempt = 8 次,`REWARD_MODE=dense`。结果:**全部 MT@2=0**(无模型达 reward=1),mean 排名 **kimi 0.565 > glm 0.411 > qwen 0.363 > flash 0.248**;所有模型 R1=1,衰减自 R2/R3;reference=1 / nop≈0.004。**与泄漏版对比**:泄漏时 glm 曾 reward=1、各模型早期里程碑靠读 scenario.json 拿满分;修复后任务显著变难,泄漏确实在抬高早期分数。**单次方差大**(glm a2 重跑很低)→ MT@k 必要性。README §5 已填此表;早期 binary/泄漏版结果仅作参考。

## 9. 常用命令

```bash
H=.venv/bin/harbor; PY=.venv/bin/python
$PY -m pytest tests/                                   # 单元测试(无 Docker)
# A+ 端到端(单 trial 动态里程碑)
$H run -e novita --env-file .env -p tasks/benchmark/multi-round-cli-demo \
    -a benchmark.interactive_agent:InteractiveUserClaude -m deepseek-v4-flash
# Design B 端到端(原生 multi-step,每步一轮 + 步间 runner)
$H run -e novita --env-file .env -p tasks/benchmark/multi-round-cli-demo-multistep \
    -a benchmark.interactive_step_agent:InteractiveStepClaude -m deepseek-v4-flash \
    --plugin benchmark.design_b_plugin:DesignBPlugin
# 判别器(A+/B 均可):只做最后一里程碑 → reward=0
$H run -e novita --env-file .env -p <task> -a benchmark.last_only_agent:LastOnlyClaude -m deepseek-v4-flash \
    [--plugin benchmark.design_b_plugin:DesignBPlugin]
$H run -p <task> -e novita --print-config              # 零成本配置预检
# 新任务端到端(A+/Design B 均可,3 个新任务同理)
$H run -e novita --env-file .env -p tasks/benchmark/<todo-tracker|repofix|pkg-wordcount> \
    -a benchmark.interactive_agent:InteractiveUserClaude -m deepseek-v4-flash
# 本地验证参考解法(无容器):解 solve.sh 到临时 ws,再跑 scorer
#   sed 's|/workspace|<tmpws>|g' tasks/.../solution/solve.sh | bash
#   $PY tasks/.../tests/scorer.py --base-dir <tmpws> --scenario <scenario> --reward-out <out>
#   (pkg-wordcount 本地验证需 venv 有 pip:.venv/bin/python -m ensurepip)
```

## 10. 相关文件索引

- 章程/框架参考:`CLAUDE.md`
- 用户手册:`README.md`
- 任务套件设计(文献调研 + 能力分类 + 任务规格):`docs/task-suite-design.md`
- T6 devteam 任务设计/验收文档(本地测试文档:里程碑需求 + verifier 评价标准 + 手动判断要点 + 真人扮演模式用法):`docs/task-devteam.md`
- 运行凭证模板:`.env.example`(真实 `.env` 已 git-ignore)
- 框架代码:`benchmark/`(A+:interactive_agent/controller/user_simulator/scenario/prompt_templates/last_only_agent/partial_support_bot/partial_ticket_system/partial_devteam;**manual_user 真人扮演模式**;B:step_driver/multi_step_trial/interactive_step_agent/design_b_plugin;**debug_long_sandbox_plugin 长沙箱观察插件**,`NOVITA_SANDBOX_TIMEOUT` 可配,见 §6.10)
- 任务(7 个):`tasks/benchmark/multi-round-cli-demo/`(基线 stats)、`todo-tracker/`(T1 持久化 CLI)、`repofix/`(T2 调试+边界+回归,`seed/` 预置坏仓库)、`pkg-wordcount/`(T3 包+pytest+入口)、`support-bot/`(T4 客服机器人)、`ticket-system/`(T5 产品开发:内部工单系统 HTTP 服务,`seed/` 预置业务文档+接口契约,`ground_truth/` 仅 verifier)、`devteam/`(T6 协同开发工具:CLI+HTML 仪表盘,无 seed/ground_truth,scorer 内即时生成输入);多步变体 `multi-round-cli-demo-multistep/`(Design B);单测:`tests/`(含每个新任务的 scorer 一致性测试)
- 端到端 job 结果:`jobs/<timestamp>/`(result.json / agent/ / verifier/;Design B 另含 `steps/*/{agent,verifier}/` per-step 轨迹与 reward)
