# Multi-turn Agent Benchmark (programming domain)

基于 [Harbor](https://github.com/laude-institute/harbor) 框架构建的编程领域 **Multi-turn 交互式 Agent Benchmark**。

核心任务为 **devteam**（团队协同开发工具）；更早的 **T1/T2/T3**（todo-tracker / repofix / pkg-wordcount）作为复杂度阶梯的简短前序。


---

## 1. 核心思想

与普通单轮任务不同：**"用户"会在 agent 完成任务过程中多次介入** —— 由一个独立 LLM（或真人，`USER_SIMULATOR=manual`）**根据 agent 上一轮的实际输出**生成下一轮自然 user 消息。评测 agent 是否能在多轮对话中：

- **持续理解上下文**（每轮只给模拟用户那一条自然消息，不重放历史；agent 必须从环境 + 新消息重建上下文）；
- **正确更新需求**（用户追加 / 修改 / 推翻旧指令）；
- **避免遗忘旧约束**（回归）；
- **最终完成编程任务**。

**介入是动态的**：user-LLM 每轮判定上一轮输出是否满足当前里程碑 —— 满意→推进下一里程碑；不满意→纠正轮（留在当前，≤ `max_corrections`）；纠正耗尽→强制推进（该里程碑由 verifier 判 0）。总轮次由 `max_rounds` 硬上限约束。

## 2. 任务设计

### 2.1 devteam（主任务）—— 团队协同开发工具

**整体需求**：为一个**小型软件开发团队**做一个**命令行协同开发工具** `devteam`，帮成员们共同管理代码。团队需要的核心能力是：
- **项目与成员管理**：能建项目、添加 / 移除成员、给成员分角色（谁能管、谁能写、谁能只看）；
- **代码协作**：一个共享的代码工作区，能提交、回滚、查看历史（相当于团队自建一个轻量版本控制）；
- **日程与概览**：排团队日程，随时一眼看到项目全貌（成员、文件、提交、近 7 天日程）；
- **写代码辅助**：质量检查（语法错误、未定义变量、TODO）与补全提示。

这些需求由**模拟用户**（团队技术负责人）在 **4 个里程碑**里逐步提出：第 1 轮只给一句话概述，agent 需先问清楚再做；之后每轮追加 / 调整一个方向，agent 在前一轮实现上继续演进，且不能破坏已有功能。

**形态**：`devteam` 是纯 Python 标准库的命令行工具（CLI），并会生成自包含的 HTML 概览页（`dashboard-<项目名>.html`）。数据持久化到当前目录 `devteam.json`，代码工作区在 `projects/<项目名>/code/`，当前操作者由 `DEVTEAM_USER` 环境变量指定（未设置默认 root 超管）。每个里程碑都是**可观测行为**（CLI 输出 / 磁盘状态 / 文件内容），verifier 对**最终**工作区逐一检查，并**重新验证之前所有里程碑**（累计回归）。各里程碑 agent 具体完成的任务如下：

**M1 `devteam_org` —— 项目与成员权限模型**
- 从零搭建 `devteam` CLI 与数据层：`project create/list/remove`（创建者自动成为 owner），数据写入当前目录 `devteam.json`（跨进程保留）；
- `member add/remove/list --project <项目名> --role owner|member|viewer`（不写 `--role` 默认 member）；
- 身份：`DEVTEAM_USER` 环境变量（未设置默认 root 超管）；
- 权限：非成员禁入、仅 owner 能管成员 / 删项目、viewer 只读。

**M2 `devteam_vcs` —— 迷你版本控制与协作**
- 代码工作区 `projects/<项目名>/code/`（团队直接写文件，agent 只管版本控制）；
- `commit <项目名> -m <消息>`：快照全部文件 + 记录提交者（`DEVTEAM_USER`）+ 打印 id；
- `history <项目名>`（新到旧，`id 提交者 时间 消息`）、`rollback <项目名> <id>`（恢复文件内容）、`file-history <项目名> <文件>`（含 unicode / 嵌套路径文件名）；
- 权限：member / owner 可提交，非成员禁入；M1 的项目 / 成员 / 权限命令全部不坏。

**M3 `devteam_schedule` —— 日程与界面**
- `event add/list/remove --date <YYYY-MM-DD> [--member <成员名>]`（日程增删仅 owner / member，viewer 只读）；
- `status <项目名>`：概览含精确计数 `成员数: N` / `代码文件数: N` / `提交数: N` + 未来 7 天日程；
- `dashboard <项目名>`：生成自包含 `dashboard-<项目名>.html`（含项目名 / 成员 / 日程）；
- `member list` / `event list` / `history` 支持 `--output-json`。

**M4 `devteam_quality` —— 质量工具与权限反转**
- `check <项目名>`：扫描 code/ 下 `.py`，报语法错误 / 未定义变量 / TODO（须区分注释与字符串；干净零输出、退出码恒 0）；
- `autocomplete <项目名> <前缀>`：收集项目里定义的函数 / 类 / 变量名，前缀匹配、字母序；
- **权限反转**：viewer 从"只读"改为"能提交"（commit / rollback），但日程管理仍仅 owner / member；非成员禁入等 M1 规则保持不变。

**多轮介入机制**：第 1 轮 = `instruction.md`（刻意简略，迫使 agent 澄清）；后续轮 = user-LLM 基于「上轮输出 + 工作区真实 diff」的判定与自然消息。支持**澄清子循环**（agent 提问 → user 按 `user_knowledge` 回答，不消耗纠正、不推进）。`benchmark/manual_user.py` 提供**真人扮演用户**模式（`USER_SIMULATOR=manual`），每轮打印里程碑需求 + 评价标准，人手判定。

**Reward 双模式**（`harbor run --ve REWARD_MODE=...`）：
- `dense`（默认）：每轮返回**连续 0-1**（该里程碑子检查通过比例），`reward` = 逐轮乘积 → 有部分分，RLVR 更友好；
- `binary`（旧）：每轮 0/1，`reward` ∈ {0,1} → 稀疏判别语义（只做最后一轮 → 0）。

### 2.2 前序任务 T1–T3（简述）

- **T1 todo-tracker**：有状态 CLI，持久化 + 过滤 / 统计 / 搜索（状态持久化、数据建模）。
- **T2 repofix**：预置坏仓库，调试 → 边界加固 → 重构 + 回归测试（调试、自我验证）。
- **T3 pkg-wordcount**：pip 可安装包 + pytest + console 入口（真实生态）。

它们与 devteam 同属复杂度阶梯，验证方法与 devteam 一致（参考解 → 1，判别器 → 0）。

## 3. 能力考察（devteam 每里程碑）

| 里程碑 | 具体考察模型哪一方面的能力 |
|---|---|
| **M1** | **准确理解需求 / 主动澄清**：brief 刻意简略（存储位置、命令命名、角色定义都不给），会问的模型靠澄清拿到精确规格；不问就猜的模型存错目录、用错角色、命令对不上 → M1 就掉分。同时考察**数据建模**（`devteam.json` 结构）与**权限模型**实现。 |
| **M2** | **上下文保持 + 增量开发 + 回归不破坏**：在 M1 的数据模型上追加 VCS，`commit/history/rollback/file-history` 必须正确操作磁盘快照（含 unicode / 嵌套文件），且 M1 的项目/成员/权限命令全部不坏。 |
| **M3** | **精确规格遵循 + 格式一致性 + 大特性追加**：`status` 必须按钉死的 `成员数: N` 等格式输出精确计数；`dashboard` 文件名必须是 `dashboard-<项目名>.html`（在 requirement / user_knowledge 里、澄清可问出）；`--output-json` 字段集合正确。格式细节拿不准却不问 → 猜错标签/文件名即失败。 |
| **M4** | **需求反转 / 遗忘被推翻指令 + 长期记忆 + 质量精度**：①viewer 从"只读"反转成"能提交"（遗忘旧规则），但日程管理仍保持 owner/member-only（**反转作用域**把握）；②M1 的核心（非成员禁入、仅 owner 管成员）必须**仍**工作（记住早期约束）；③`check` 必须**精确** —— 语法错误/未定义变量要报，字符串里的 `"TODO"` 不能误报、干净文件不能误报。 |

实证倾向：澄清/格式把握影响后续轮次；clean 任务下所有模型都难以全轮通过（见 §5 的 MT@2 结果），衰减集中在 M2–M4。

## 4. 测试覆盖

- **verifier（scorer.py）**：每个里程碑是一个 `(label, 检查)` 子检查列表，对**最终**工作区运行；`reward` = 各里程碑分数乘积。子检查覆盖：CLI 命令实际行为（stdout / 退出码 / 磁盘状态 / 文件内容）、权限正反断言（非成员读+写全拦、仅 owner 管成员、viewer 反转作用域）、精度断言（check 干净零输出 / 字符串 TODO 不误报 / 未定义变量需真 AST）、隐藏边界（空项目提交、回滚坏 id、unicode+嵌套文件、移除不存在成员/日程报错）。
- **无硬编码测试输入**：项目/成员名从名字池 seeded-RNG 抽样，日程日期相对 `today` 生成，代码文件（注入问题 / 生成标识符）由 scorer 现场写入。
- **判别器验证**：`benchmark/partial_devteam.py`（只做 M1+M2）→ reward=0（二进制模式），证明任务区分"只做部分里程碑"与"完整多轮"。
- **单元测试**（无 Docker）：`tests/` **118 项全通过** —— 控制器 / 澄清子循环 / 人工模式 / 每任务 scorer 一致性 + reward 双模式。

## 5. 模型运行结果

> 固定 user-LLM = kimi-k3，`REWARD_MODE=dense`，**MT@2**（每模型任务级独立重跑 2 次，MT@k = 达到 reward=1 的占比）。

| 模型 / 后端 | MT@2 | mean(dense) | R1 | R2 | R3 | R4 | 首败轮 | 澄清/纠正/分歧 |
|---|---|---|---|---|---|---|---|---|
| moonshot/kimi-k3 | 0.000 | **0.565** | 1.000 | 1.000 | 0.725 | 0.692 | R3 | 0 / 3 / 2.5 |
| zai/glm-5.2 | 0.000 | 0.411 | 1.000 | 0.633 | 0.575 | 0.692 | R2 | 2 / 3.5 / 2 |
| aliyun/qwen3.5-flash | 0.000 | 0.363 | 1.000 | 0.600 | 0.825 | 0.654 | R2 | 3 / 2.5 / 0.5 |
| deepseek-v4-flash | 0.000 | 0.248 | 1.000 | 0.500 | 0.900 | 0.615 | R2 | 1 / 2 / 1 |
| **reference**(oracle) | 1.000 | 1.000 | 1 | 1 | 1 | 1 | — | — |
| **nop**(空) | 0.000 | 0.004 | 0.385 | 0.200 | 0.150 | 0.308 | R1 | — |

**观察**：①**clean 任务下 4 个模型全部 MT@2=0** —— 没有一个在两 attempt 里达到 reward=1，任务比泄漏版（曾让 glm 全过）**难得多**；②按 mean dense 排名 **kimi > glm > qwen > flash**；③所有模型 R1=1（M1 权限模型最容易），**衰减从 R2/R3 开始**（M2 的 VCS / M3 的精确格式 / M4 的反转作用域），对应 EvoCode-Bench 的"多轮衰减"发现；④单次运行方差大（如 glm a2 重跑很低），印证 **MT@k 的必要性**；⑤reference=1 / nop≈0 基线正确，任务"难而可解"。


## 6. 仓库结构与各部分作用

```
proj/
├── benchmark/                       # 框架代码(需在 PYTHONPATH 上)
│   ├── interactive_agent.py         # InteractiveUserClaude:多轮循环 + claude-code 驱动,每轮把 user 消息作为下一条指令传给 agent,抓取工作区 diff
│   ├── controller.py                # TurnController:里程碑状态机(推进/纠正/澄清/强制推进)
│   ├── user_simulator.py            # UserSimulator:LiteLLM 判定 + 生成自然 user 消息
│   ├── manual_user.py               # 真人扮演用户模式(USER_SIMULATOR=manual)
│   ├── prompt_templates.py          # user-LLM 提示词(判定 + 消息;含"上手实测 + 至少一条建议"规则)
│   ├── scenario.py                  # Milestone/Scenario 模型(schema 校验)
│   ├── run_model_compare.sh         # 多后端多模型对比 grid(deepseek/zai/moonshot/aliyun)
│   ├── debug_long_sandbox_plugin.py # 长沙箱 + 无单命令超时插件(LongSandboxPlugin)
│   ├── partial_devteam.py           # 判别器(只做 M1+M2)
│   └── step_driver / multi_step_trial / interactive_step_agent / design_b_plugin.py
│                                     # Design B(实验性):原生 multi-step 路径,devteam 本身走 Design A+
├── tasks/benchmark/devteam/         # 主任务
│   ├── instruction.md               # 第 1 轮初始任务(刻意简略)
│   ├── task.toml
│   ├── environment/{Dockerfile, scenario.json}  # scenario.json 进容器 /scenario.json(不在 /workspace,防 agent 偷看未来里程碑)
│   ├── solution/solve.sh            # 参考解法(全 4 里程碑)
│   └── tests/{test.sh, scorer.py}   # 逐里程碑累计计分 → reward.json
├── tasks/benchmark/{todo-tracker, repofix, pkg-wordcount}/   # T1–T3 前序任务
├── tests/                           # 无 Docker 单元测试
├── docs/task-devteam.md             # devteam 验收文档(每里程碑需求 + 评价标准 + 手动判断)
├── docs/task-suite-design.md        # 任务套件设计(文献调研 + 能力分类)
└── .env.example                     # 运行凭证模板
```

> **Design A+ 与 Design B（devteam 的取舍，基于 demo 实测）**：

| 维度 | Design A+（devteam 采用） | Design B（实验性） |
|---|---|---|
| 载体 | 单 trial 内自定义 agent 驱动所有轮次（`InteractiveUserClaude` + `TurnController`） | Harbor 原生 multi-step：每步 = 一轮，步间 `StepDriver` 判定 + 生成下一步指令（`benchmark/{step_driver,multi_step_trial,interactive_step_agent,design_b_plugin}.py`） |
| 终端 reward | 参考解 1 / 判别器 0 | **与 A+ 完全一致**（demo 实测：参考解逐步 `{1,0,0}→{1,1,0}→{1,1,1}` 终值 1；判别器每步 `{0,0,1}` 终值 0） |
| 每步过程信号 | 无（终端快照；有每轮 user 判定 `decisions` + dense 每轮键） | ✅ **每步 verifier 分数**（客观的逐步里程碑满足度，RLVR process reward） |
| 运行时开销 | demo 参考解 4–6 min | **~1.5× 慢**（demo 9 min；每步 verifier + 步间归档；判别器 30s→4m） |
| 动态轮次 / 澄清 | 原生支持（轮数每次运行不同） | 需预建物理步数；纠正轮"留同里程碑"、澄清子循环在 step 模型里映射别扭 |
| 成熟度 | 主线，devteam/T1–T3 全部验证 | 实验性、纯增量、可回退（demo 专用） |

两个变体在 demo 上的**终端判定完全等价**；B 的唯一实质优势是**每步 verifier 过程奖励**，但代价是 ~1.5× 运行时开销 + 动态轮次/澄清的 step 模型错配。对 devteam 这种每轮已 ~8-16 分钟、按量计费的任务，这个开销被放大。**仅当 RLVR 训练明确需要每步过程奖励（逐步信用分配）时**，才值得建一个 devteam 的 B 变体目录（像 demo 那样纯增量、可回退）对比产物，而非直接替换；A+ 的 `reward.json` 多键 + `decisions` 日志已提供大部分 per-round 诊断信号。

### 环境配置

```bash
# 1. 安装依赖(用 uv,不要用 pip install harbor —— 镜像源过期/超时)
uv sync

# 2. 配置运行凭证
cp .env.example .env   # 填入下列 key
```

`.env` 需要的凭证（真实 `.env` 已 git-ignore）：

| 变量 | 作用 / 获取 |
|---|---|
| `NOVITA_API_KEY` | 云沙箱 + user-LLM 计费（novita.ai Key Management，`sk_` 开头；**账号必须有余额**，否则 403 NOT_ENOUGH_BALANCE） |
| `USER_LLM_MODEL` | 模拟用户 LLM，如 `openai/moonshotai/kimi-k3` —— **必须带 LiteLLM 认识的 provider 前缀**（裸 `moonshotai/...` 会让 LiteLLM provider 解析崩溃） |
| `USER_LLM_API_BASE` / `USER_LLM_API_KEY` | user-LLM 端点，如 Novita 的 `https://api.novita.ai/openai` |
| `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` | **agent（claude-code）在沙箱内的 LLM 后端**。host OAuth 不会带进沙箱；缺了 claude 报 `Not logged in` → verifier 全 0。本项目用 DeepSeek 的 Anthropic 兼容代理（`https://api.deepseek.com/anthropic`） |

执行要点：

- **执行 provider = Novita 云沙箱**（`-e novita`）：本机 Docker daemon 虽在运行，但当前用户无 root/socket 权限且无 sudo。Novita 把任务 `environment/Dockerfile` 云端构建成模板（支持 FROM/RUN/COPY/ADD/WORKDIR/USER/ENV/CMD），按 `environment_name + env_hash + key 尾` 缓存；**scenario.json 变了会重建模板（几分钟）**；沙箱最长 1h 自动销毁，按运行时长 + LLM 调用计费。
- **agent 模型必须用 DeepSeek 系**（如 `-m deepseek-v4-flash`），不能用 `claude-*` 名字（DeepSeek 端点上 `claude-sonnet-5` 会被静默别名成 flash）。
- **长沙箱观察跑**：加 `--plugin benchmark.debug_long_sandbox_plugin:LongSandboxPlugin`（默认 2h，`NOVITA_SANDBOX_TIMEOUT` 可配；1h 沙箱只够约 6 轮，复杂任务 4 里程碑跑不完）。
- **reward 双模式**：`--ve REWARD_MODE=dense`（默认）或 `--ve REWARD_MODE=binary`（旧 0/1）。

### devteam 任务测试教程

```bash
PY=.venv/bin/python
```

**① 单元测试（无 Docker，最快）**
```bash
$PY -m pytest tests/
```
期望：全部通过（控制器 / 澄清子循环 / 人工模式 / 各任务 scorer 一致性 + reward 双模式）。

**② 参考解本地验证（无容器）—— 验证任务"可解"**
```bash
tmpws=$(mktemp -d)
sed 's|/workspace|'"$tmpws"'|g' tasks/benchmark/devteam/solution/solve.sh | bash
$PY tasks/benchmark/devteam/tests/scorer.py --base-dir "$tmpws" \
  --scenario tasks/benchmark/devteam/environment/scenario.json --reward-out "$tmpws/reward.json"
cat "$tmpws/reward.json"
```
期望：`round_1..4=1, reward=1`。

**③ 判别器本地验证 —— 验证任务能区分"只做部分里程碑"**
用 `benchmark/partial_devteam.py` 里的 `_PARTIAL`（仅 M1+M2、viewer 仍只读）写入临时 ws，再跑同一 scorer。
期望：`round_1=1, round_2=1, round_3=0, round_4=0, reward=0`（乘积捕获缺失里程碑）。

**④ Harbor 配置预检（零成本）**
```bash
.venv/bin/harbor run -p tasks/benchmark/devteam -e novita --print-config
```
期望：JobConfig 解析通过，不发 API 请求。

**⑤ devteam 端到端（agent=claude-code，user=user-LLM；按量计费，需 .env 凭证）**
```bash
PYTHONPATH=. .venv/bin/harbor run -e novita --env-file .env \
  -p tasks/benchmark/devteam -a benchmark.interactive_agent:InteractiveUserClaude \
  -m deepseek-v4-flash --plugin benchmark.debug_long_sandbox_plugin:LongSandboxPlugin
```
期望：完整跑完 4 里程碑（约 30-60 分钟），`jobs/<timestamp>/verifier/reward.json` 里有 `round_1..4` 与 `reward`；复盘用 `jobs/<timestamp>/agent/interactive_transcript.json`（transcript + decisions，含 workspace_evidence）。

**⑥ 真人扮演用户（不依赖 user-LLM）**
```bash
USER_SIMULATOR=manual PYTHONPATH=. .venv/bin/harbor run -e novita --env-file .env \
  -p tasks/benchmark/devteam -a benchmark.interactive_agent:InteractiveUserClaude -m deepseek-v4-flash
```
每轮终端打印当前里程碑的 user_intent / requirement（评价标准）/ user_knowledge + agent 输出 + 工作区 diff；人手输入 `s <消息>`（满意推进）/ `c <消息>`（纠正）/ `a <消息>`（回答澄清），或严格 JSON。

**⑦ 多模型对比 grid（固定 user-LLM，只变 agent 模型）**
```bash
./benchmark/run_model_compare.sh
# 或指定模型:AGENT_MODELS="deepseek/deepseek-v4-flash zai/glm-5.2 moonshot/kimi-k3 aliyun/qwen3.5-flash" ...
```
期望：逐模型打印 reward / 逐轮 / agent 轮数 / 澄清 / 纠正 / 强制推进 / judge-vs-scorer 分歧 / 时长 / 费用。默认 deepseek/zai/moonshot/aliyun 4 后端；`REWARD_MODE=binary` 复现旧 0/1 语义。

**⑧ reward 双模式对比**
```bash
.venv/bin/harbor run -e novita --env-file .env -p tasks/benchmark/devteam \
  -a benchmark.interactive_agent:InteractiveUserClaude -m deepseek-v4-flash --ve REWARD_MODE=dense
# --ve REWARD_MODE=binary 为旧 0/1
```
期望：dense 每轮有部分分（如判别器 round_3=0.3）、binary 复现 0/1。

## 7. 不足与展望

1. **覆盖任务量过少**：目前只有一个深度任务（devteam）+ 三个简短前序（T1–T3）。为得到更稳健的模型排名与能力画像，需要扩充到更多真实编程场景的任务。
2. **MT@k 评估刚起步**：已按 EvoCode-Bench 的做法给 `run_model_compare.sh` 加入多轮采样评估（默认 `MT@2`，任务级独立重跑，MT@k = 达到 reward=1 的占比；附 per-round 均值衰减曲线、首败轮次、reference/nop 基线）。受算力限制目前只跑 MT@2，后续可扩展 k 与更多轮次。
3. **对用户的模拟还需提升可靠性与真实性**：实测 user-LLM 存在**过度接受**（judge-vs-scorer 分歧：user 判定满足但 verifier 判 0），且偶尔会"发明"ground-truth 之外的要求；还出现过**判定动作标错** —— kimi 一次把 `action` 标成 `answer`（澄清）但消息内容其实是"推进到下一里程碑"的需求，控制器按 answer 停在当前里程碑，可能扭曲了交互流。需更强的忠实度护栏、`action` 与 `message` 的一致性校验、judge-vs-scorer 分歧报告作为质量哨兵，并用多 user-LLM 模型校准（"Lost in Simulation" 敏感性）。真人扮演模式（`USER_SIMULATOR=manual`）可作为高可靠性的替代与校验手段。
