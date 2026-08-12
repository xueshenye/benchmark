# 多轮交互式编程 Agent Benchmark:任务套件设计

> 状态:**设计稿**(文献调研 + 能力分类 + 任务规格)。配套实现见 `tasks/benchmark/`。
> 本文件回答三个问题:(1) 该考哪些能力、依据是什么(文献/基准);(2) 用什么样的任务形态去考; (3) 每个任务的具体规格与评测标准。

---

## 1. 背景与目标

`PROJECT_STATE.md` §7 的阻塞项是"待用户补充的真实 benchmark 任务内容(当前只有 demo 的 stats CLI)"。本设计的核心约束:

- **框架能力**:每个 agent 轮 = 一次独立 `claude --print`,共享容器状态,**不重放对话历史**;user-LLM 每轮基于 agent 实际输出 + 工作区真实 diff 判定 `{"satisfied":bool,"message":str}`;里程碑累计,`reward = 各轮得分乘积`。
- **对任务的要求**(综合文献):里程碑需求**必须是可观测行为**;每个轮次引入**恰好一个新/改需求**(渐进式揭示,不在一开始倾倒全部规格);**ground-truth 与 user-LLM 转述后的自然消息一致**(PROJECT_STATE.md §6.4 教训:格式细节必须写进 `requirement` **和** `user_intent`);verifier 必须**检查真实工件而非 agent 自报**。
- **成本**:Novita 沙箱 + 3 次 LLM 调用/轮(agent + user-LLM)按量计费 → 任务应在**本地**(solve.sh + scorer + 单测)先行验证,端到端每个任务跑 1-2 次。

## 2. 文献与现有基准调查

> 详细出处见 §6 参考文献。此处给结论性摘要。三条研究线(能力、基准形态、LLM 模拟用户)各由一个独立调研 agent 完成,要点在此汇总。

### 2.1 基准版图(我们的设计在什么位置)

**最终态 + 执行打分(单轮)**:SWE-bench(2,294 真实 issue,FAIL_TO_PASS + PASS_TO_PASS,补丁过执行测试才算解决;Verified 为 500 题人工过滤子集)、SWE-bench Multimodal(617 视觉任务)、SWE-bench Pro(1,865 题、长程多文件)、AppWorld(9 应用 457 API,**state-diff 检测"collateral damage"**)、CORE-Bench(全对才 1.0)。这些无用户介入;其"执行检查 + 回归断言"是各里程碑检查的原型。

**多轮 / 交互式(与我们的设计最接近)**:
- **SWE-Interact**(Scale, 2026, arXiv:2606.30573):与我们的 `workspace_evidence` 完全同构——**user 模拟器在回复前检查 agent 的真实工作区**;persona 条件化("Expert Nitpicker");从模糊指令开始、**每个纠正轮揭示一个缺失需求**;最终仍用原始目标的测试打分(模拟器管交互、测试管评分)。核心发现:**单轮能过 ≠ 多轮能过**(最佳模型单轮 ~50% → 多轮 ~25%);失败模式 = 过度 agentic 编码 + 需求遗忘。
- **SWE-Together**(2026, arXiv:2606.29957):从 11,260 段真实 user-agent 会话重建 109 个任务;user 动作空间 = **Redirect / Correction / New Requirement**(最接近我们的 satisfied/unsatisfied 状态机);**Rubric Judge** 打分;以**所需纠正轮数**作 UX 指标;**46% 图灵通过率**验证模拟器保真度。
- **τ-bench**(Sierra, 2024, arXiv:2406.12045):LLM 用户 + tool-agent 多轮 POMDP;用户有 persona + 已知/未知信息 + 行为约束;评分 = **最终状态**,不用 LLM-as-judge;指标 **pass^k**(用户 LLM 引入随机性 → 需多次运行)。
- **Asuka-Bench**(2026):Code Agent → UI Agent → **User LLM 把 pass/fail 转成自然语言反馈**的循环;**784 条标准构成 DAG**(依赖排序——里程碑前置条件的模型);人工验证 eval 93% 准确率。**反例警告**:若 user 反馈与评测标准同构,轮次退化成 token 更贵的重试循环。
- **ClarEval**(2026):注入三种歧义(缺失目标/缺失前提/术语歧义),测**澄清能力**;指标 ATC(澄清轮数)与 KQC(关键问题覆盖率)。
- **MINT**(ICLR 2024):GPT-4 模拟用户反馈,额外反馈 +2-17%;**单轮强 ≠ 多轮强**的最早证据。
- **EvoCode-Bench**(2026, arXiv:2605.24110):26 个有状态任务 × 5-15 轮(Harbor runner,per-round reward),**累计可执行测试**;第 5 轮后累计通过率不足第 1 轮一半;单轮/多轮指标对 agent 排序不一致。

**多环境通用 agent**:AgentBench(8 环境、**完成原因分类 CLE/IF/IA/TLE/Complete**)、InterCode(bash/sql/python,POMDP,部分学分 = 通过测试比例;单轮 → 多轮 SQL 9.1%→73.7%)、WebArena(**确定性 state-based 打分替代 LLM judge,false negative 降 ~11%**)、WorkArena(DB 行校验)。

**RLVR / 可验证奖励(训练侧,警示)**:SWE-Gym / SWE-RL / SWE-smith 用单元测试或补丁相似度做 verifiable reward;**记录在案的奖励劫持 = agent 学会删除失败测试**(后以 code-style + coverage 约束压制)。→ 我们的逐里程碑检查**必须含 PASS_TO_PASS 型回归断言**,不能只查新需求。

**对设计的定位结论**:
1. "**每里程碑二值 × 乘积 reward**"在文献中是**少见且可辩护**的选择——它直接修掉最终态基准(TauBench/AppWorld)的盲点(无法区分"只完成最后一轮"与"全部完成")。
2. 领域共识(WebArena Verified / AppWorld):**里程碑可机器检查时,优先确定性状态验证,而非 LLM judge**——我们的 scorer 已经这么做了。
3. **本设计最大的风险是 user-LLM 本身,不是 agent** —— 文献反复证明模拟器过于合作、对所选模型敏感(±9pp)、存在人口学偏差、τ²-bench 对话 47% 含模拟器错误。把 user 判定锚定在真实 diff 上正是对的缓解,但仍应(a)报告用了哪个 user-LLM,(b)把 `satisfied` 当运行特性而非 ground truth,**里程碑执行检查永远是最终裁判**。
4. **反馈/规格同构**(Asuka-Bench 反例):若 user 纠正消息只是里程碑检查的转述,轮次退化成重试循环。我们的忠实度护栏(不凭空加需求)+ "基于真实 diff 判定"缓解之;还需实证验证**纠正确实新增信息**。

### 2.2 能力分类(应考什么)

综合 AgentBench(KDD'25 综述,arXiv:2507.21504)、τ-bench、SWE-Interact 等,把"多轮编程 agent 的能力"拆成 10 个维度(与我们的框架能考到的映射):

| # | 能力 | 多轮语境下为何重要 | 任务形态(来自文献) | 本套件覆盖任务 |
|---|---|---|---|---|
| 1 | 指令跟随 / 需求解读 | 每轮只有一条新自然消息,无历史重放;解读错则污染后续所有轮 | 渐进式需求细化(SR-Eval)、模糊→精确(SWE-Interact) | 全部 |
| 2 | 上下文保持(跨轮) | 第 1 轮的需求第 5 轮仍须成立;只能从环境+消息重建上下文 | 跨轮约束(PASS_TO_PASS)、隐藏状态追踪(τ-bench) | 全部(核心) |
| 3 | 规划与分解 | 多轮特性 = 多步工程;区分"补最后一轮"与"真正做完" | 长程软件演化(SWE-EVO,~21 文件/任务)、显式 todo 列表 | repofix R3、todo R4 |
| 4 | 工具 / CLI / 生态 | 真实终端:git、pip、pytest、构建 | terminal-bench、InterCode-Bash、CORE-Bench | pkg 全套 |
| 5 | 调试与错误恢复 | 每轮新需求可能破坏旧代码;早期错误会传播 | 植入边缘 bug、Reproduce-then-fix(SWE-bench FAIL_TO_PASS)、多 bug 校准 | repofix 全套 |
| 6 | 回归避免 / 向后兼容 | **reward=乘积 的直接判据**;破坏第 1 轮而完成第 3 轮 = 0 | 扩展 CLI/库但保留旧 flag、PASS_TO_PASS vs FAIL_TO_PASS | 全部(核心) |
| 7 | 多文件重构 / 接口变更 | 改数据/接口并同步所有调用点;跨文件一致性 | RefactorBench、签名变更 + 全仓替换 | repofix R3(重构)、todo(可选) |
| 8 | 测试行为 / 自我验证 | agent 自己的测试是回归避免的机制;verifier 必须防"建到测试" | 每轮后自跑测试(agent-verify)、SWE-AGI public/private 测试拆分 | repofix R3、pkg R2 |
| 9 | 与用户沟通 / 澄清 | agent 选择"问"vs"假设"可评分;模糊轮次是旋钮 | Ambig-SWE(交互提升 +74%)、underspecified 任务 | **T4 support-bot / T5 ticket-system**(澄清子循环:agent 提问 → user 据 `user_knowledge` 回答,不消耗纠正、不推进) |
| 10 | 非纯编码技能 | DB、shell、文件、数据 | InterCode-SQL、terminal-bench sanitize-git-repo | todo(状态持久化)、repofix(数据) |

### 2.3 累计里程碑设计教训

1. **每轮需求 = 可观测行为**,附累计测试套件(EvoCode-Bench)。
2. **渐进式揭示**,绝不第一轮倾倒全部规格(SR-Eval / SWE-Interact)。
3. **校准单轮→多轮落差**:单轮能解不代表任务"太简单";强 agent 完整做完应得 1、只做最后一轮应得 0(判别器已保证)。
4. **为回归设计,不只做功能增**:PASS_TO_PASS 型检查(隐藏,agent 看不到内容)。
5. **严格过滤**:SWE-bench Verified 丢弃 ~68% 采样任务(欠规格/不公平/不可解);每个里程碑都要问:在给定环境下无歧义吗?测试公平吗(不依赖求解器内部)?时间/预算内可解吗?
6. **verifier 查真实工件,不查 test 信号**:"Building to the Test"(arXiv:2606.28430)证明 agent 会针对可见测试过拟合,产出死工件。对策:ground-truth 检查放 verifier(不进工作区)、断言真实行为(CLI stdout、文件状态)、保留隐藏边界测试(HumanEval+ 教训)。
7. **区分"没做完"与"做错了"**:二进制逐轮会掩盖部分进展;若里程碑过大,可轮内拆细目给部分分,但**轮间语义保持稀疏**以让"回归避免"成为主导信号。
8. **警惕错误传播**:早期轮失败会级联进 user-LLM 的纠正消息;`force-advance` 路径(已实现)避免一轮坏掉全局退化的局面。
9. **模拟用户质量是命门**:保持忠实度护栏(不凭空添加 ground-truth 之外的需求);τ-bench 显示"无视策略/ground-truth"是**模拟用户自身**的首要失败模式,不只 agent。

### 2.4 user-LLM 判定的可靠性(文献实证)

- **LLM-judge 在代码任务上不可全信**:即便最强 judge(GPT-4-turbo)也常误判代码正确性;judge ≈ 执行检查仅限简单/中等难度,难度升高即发散("Code Verification Strategies", ICLR 2026)。
- **核心对齐规则**(Microsoft):训练/对话 grader 与评测方法必须产生相同排序(校准到 Spearman ρ≥0.8)。
- **对策(已部分实现,§5 列出缺口)**:
  1. 把 `satisfied` 视为**对话控制信号**(推进/纠正/强制推进),**reward 仍由执行检查决定** —— 我们已如此(τ-bench / SWE-Interact 同款分离)。
  2. 默认保守:没有针对每条 stated criterion 的具体 diff 证据就 `satisfied=false`(我们已有 workspace_evidence)。
  3. **judge/speaker 分离**:判定(对齐 ground truth)与措辞(自然消息)分两个模块,防止判定被语气漂移。
  4. 防泄漏:user-LLM 只看到 milestone 的 `user_intent` + 工作区 diff,**绝不看到隐藏 scorer**。
  5. **逐轮 judge-vs-scorer 分歧追踪**:记录 user-LLM 的 satisfied 与 verifier 实际 round_N,输出分歧报告(哪些轮 user 说满足但执行分 0,反之亦然)——这是"user-LLM 发明未评分需求"的哨兵。
  6. 多模型稳健性:抽一小样本用第二个 user-LLM 跑,对比 reward 分布("Lost in Simulation" 显示 ±9pp 的用户模型敏感性)。
  7. 防 sycophancy / 奖励劫持:测试放 agent 工作区之外、diff-guard 测试文件改动、user 不得仅凭 agent 自述推进。

---

## 3. 任务套件总览(复杂度阶梯)

六个任务构成从易到难的阶梯,覆盖 §2.2 的 10 个能力维度。**工作区起点**、**状态性**、**工具链**、**模糊度**逐级加深。

| 任务 | 目录 | 轮次/里程碑 | 工作区起点 | 考察重点 | 复杂度 |
|---|---|---|---|---|---|
| **T0 stats** | `tasks/benchmark/multi-round-cli-demo/` | 3 | 空 | 指令跟随、格式约束、回归 | ★ 基线(已存在) |
| **T1 todo** | `tasks/benchmark/todo-tracker/` | 4 | 空 | 状态持久化、数据建模、过滤/统计、回归、跨进程一致性 | ★★ |
| **T2 repofix** | `tasks/benchmark/repofix/` | 3 | **预置坏仓库 + 测试** | 调试、边缘用例、自我验证、重构、回归测试 | ★★★ |
| **T3 pkg** | `tasks/benchmark/pkg-wordcount/` | 3 | 空 | 真实生态(git/pip/pytest)、包结构、CLI 入口、自测 | ★★★ |
| **T4 support-bot** | `tasks/benchmark/support-bot/` | 4 | **预置知识库 + 订单 API mock + docs** | 客服机器人:知识库问答、订单 API 集成、包重构+批量、多语言+转人工;**长上下文/真实应用、主动澄清、需求变更与大幅重构、长期记忆 + 遗忘被推翻规则** | ★★★★ |
| **T5 ticket-system** | `tasks/benchmark/ticket-system/` | 4 | **预置业务文档 + 接口契约 + 样例导出** | **产品开发类**(内部工单系统 HTTP 服务):CRUD → 工作流/搜索/筛选 → 包重构+SQLite+SLA → **软删除反转**+统计;**零到一产品构建、真实用户面(HTTP)、真实数据层、verifier 起真实服务器端到端检查、需求反转** | ★★★★★ |

> 命名:tasks 用 `todo-tracker` / `repofix` / `pkg-wordcount`(Harbor `task init` 需要 `<org>/<name>`;本项目沿用 `benchmark/<name>` 的本地目录约定,见 `tasks/benchmark/`)。

## 4. 各任务规格

### 4.1 T1 todo-tracker(有状态 CLI,持久化 + 增长语义)

**用户 persona**:一位产品经理,负责一个团队的日常任务管理;在意数据可见性(要求任务存成文件)、在意 CLI 稳定不破坏已有命令。

**R1 `tracker_crud`(初版)**
- 命令 `todo add "<任务描述>"`、`todo list`、`todo done <id>`、`todo`(无参打印用法)。
- 任务有递增 id、描述、`status`(pending/done)、`created_at`。
- **数据持久化到当前目录的 `todos.json`**(用户可见的文件);跨进程调用状态保留。
- 仅 Python 标准库。

**R2 `tracker_all_stats_json`**
- `todo list --all`(含 done,显示状态)、`todo stats`(total/pending/done 计数)。
- `todo --output-json list` 输出 **JSON 数组**(任务对象含 id/description/status/created_at;空列表也输出 `[]`)。
- 默认纯文本输出不变;R1 命令全保留。

**R3 `tracker_priority`**
- `todo add "<task>" --priority high|medium|low`(默认 medium)。
- `todo list --priority high` / `todo list --status done` 过滤;JSON 输出含 `priority` 字段。
- `stats`/`--all`/`done` 等旧行为不变。

**R4 `tracker_report_search`**
- `todo report`(按 priority 分组的计数)、`todo search "<关键词>"`(描述大小写不敏感匹配)。
- `--output-json` 对 report/search 同样生效(数组);全部旧行为不变。

**scorer 要点**(`tests/scorer.py`):全部检查在 `tempfile.TemporaryDirectory` 内以该目录为 `cwd` 运行子进程,天然隔离;持久化检查 = **同一目录多次独立进程调用**(add→list→done→list --all 状态连贯);JSON 检查解析 stdout 为数组并断言字段;每个 check 同时验证"新特性 + 旧行为仍成立"(回归)。ground-truth 细节(文件位置 `todos.json`、JSON 永远数组、priority 默认值、字段名)同时写进 `requirement` + `user_intent`。

### 4.2 T2 repofix(调试 + 边界 + 回归;mini-SWE-bench 形态)

**形态**:Dockerfile 通过 `COPY` 把一个**预置坏掉的仓库**烤进 `/workspace`(内含 `pipeline.py` 之类的 CSV 处理工具 + 2 个植入 bug + 一个可见的 `tests/test_pipeline.py`,含失败用例)。agent 从"已有代码 + 失败测试"开始——**与 T0/T1 的空工作区本质不同**,考理解既有代码。

**用户 persona**:数据分析师,报 bug 时描述症状不指行号;重视数据正确性与后续维护。

**R1 `repofix_basic`**:修复使提供的可见测试通过、且对正常输入输出正确。verifier:跑可见测试 **+** 隐藏行为检查(防止"建到测试")。
**R2 `repofix_edge`**:用户报边界用例挂:空文件、仅表头、列含非数值、缺列、Unicode。verifier:隐藏边界输入逐项检查。
**R3 `repofix_regression`**:用户要求补一个 `tests/test_regression.py` 覆盖上述修复并重构(抽取函数)便于维护,**行为不能变**。verifier:断言回归测试文件存在且 `python3 -m pytest` 全绿,隐藏检查仍过。

**scorer 要点**:bug 设计成"同一文件内 2 个独立 bug"(借鉴 terminal-bench-2 的 Deployment Health Validator:5 个独立错点),校准到"多数 agent 修部分不修全部";可见测试与隐藏检查并存;`MAX_WORKSPACE_EVIDENCE_CHARS` 截断下 diff 可控(改动集中在一个文件)。

### 4.3 T3 pkg-wordcount(真实生态:包结构 + pytest + CLI 入口)

**形态**:空工作区起,构建一个小型可安装 Python 包 `wordcount`(文本词频统计)。Dockerfile 预装 `python3-pip` 与 `python3-pytest`(网络 public)。

**用户 persona**:工程团队负责人,在意可复现安装、测试覆盖、命令行可用。

**R1 `pkg_api`**:建包,公开 API `wordcount.count(text) -> dict[str,int]`(大小写不敏感、忽略标点),`python3 -c "import wordcount"` 可从任意 cwd 导入;含 `pyproject.toml` 元数据。
**R2 `pkg_tests`**:补 `tests/` pytest 用例并全绿;新增公开函数 `top_words(text, n)`(Top-N);`count` 行为不变。
**R3 `pkg_cli`**:`pyproject.toml` 加 `[project.scripts]` 入口,`wordcount <file>` 命令可用;`pip install -e .` 后 CLI/API 都可用;pytest 仍全绿。

**scorer 要点**:verifier 在容器内 `pip install -e /workspace --quiet` 后跑 `python3 -c "import wordcount"` 与 CLI,并 `python3 -m pytest -q` 检查退出码;同时用隐藏输入直接断言 `count`/`top_words` 行为(防"只写测试"型作弊)。

### 4.4 T4 support-bot(客服机器人:长上下文 + 澄清 + 重构 + 长期记忆)

**形态**:空工作区,但预置了"用户提供的材料"——`knowledge_base/`(商品/政策/售后)、`docs/api.md`(内部订单接口规格)、`mock_api/`(订单接口的本地模拟服务)。agent 构建一个**真实可用的客服机器人**,跨 4 个里程碑演进。这是套件中**最复杂、最贴近真实场景**的任务,四个考察目标:

1. **长上下文/长程**:机器人是一个有实际意义的应用(知识库检索 + HTTP API 集成 + 批量 + 多语言),每个里程碑工作量都大,workspace 持续增长,agent 每轮都要从环境重建上下文。
2. **准确理解需求 / 主动澄清**:round-1 `instruction.md` 刻意**简略模糊**(能力 #2);agent 必须主动提问(知识库在哪?用什么实现?语言?答不上来怎么办?),user 通过**澄清子循环**(框架新能力)回答——`user_knowledge` 里的信息只在 agent 问起时揭示。
3. **灵活应对需求变更**:M2 追加订单查询(引入 HTTP API,agent 需发现/询问接口);M3 **大幅重构**(单脚本 → 可导入包 + 批量模式 + pytest);M4 细节修改 + 规则变更(多语言)。
4. **长期记忆 + 遗忘被推翻指令**:M4 **推翻了 M1 的"始终中文"规则**(改为"跟随客户语言"),scorer 同时要求英文问题→英文回答、中文问题→中文回答(双重约束逼 agent 既保留 M1 的上下文又更新规则);未知问题 → 转人工 + 写 `escalations.log`(旧"无法回答"兜底被取代)。verifier 对最终状态跑全部 4 个里程碑检查(累计回归 + 乘积 reward),所以"忘了 M1"或"没忘旧规则"都会被捕获。

**里程碑**:
- **R1 `kb_bot`**:CLI `support-bot "<问题>"` 基于知识库用中文回答;查不到 → 礼貌兜底,不编造。
- **R2 `api_orders`**:订单数据只在内部 API(不进入知识库),`SUPPORT_API_BASE`(默认 `http://localhost:8123`)配置地址;查订单真实调 API 并把状态译成中文;404 → 礼貌告知。
- **R3 `batch_refactor`**:重构为可导入的 `support_bot` 包 + `--batch q.txt -o a.txt`(逐行、顺序一致)+ pytest 全绿;单问/查订单不变。
- **R4 `lang_escalate`**:英文问题英文答(取代"始终中文");未知问题 → 转人工话术 + 追加 `escalations.log`;其余功能全保留。

**scorer 要点**:测试输入全部**运行时生成**——从隐藏的 `ground_truth/facts.json`(zh/en 事实)用固定种子随机抽样;订单数据由 verifier 自己的 mock 实例 serve(**合成订单**,agent 无法预知/篡改);知识库必须与 `ground_truth/` **字节一致**(改用户材料 → 该轮 0);`python3 -m pytest -q` 与 `import support_bot` 作 R3 检查;入口发现跳过不存在的候选文件(§6.9 教训)。

### 4.5 T5 ticket-system(产品开发类:零到一产品构建 + 真实数据层 + 需求反转)

**形态**:空工作区,但预置了"用户提供的材料"——`docs/README.md`(业务背景)、`docs/api.md`(**v1 HTTP 接口契约**)、`docs/tickets_export.json`(历史导出样例)。agent **零到一构建一个内部工单系统(HTTP 服务)**,跨 4 个里程碑演进。这是套件中**最贴近真实产品开发**的任务,对应调研结论(见 §1 背景):真实用户面(HTTP 服务器 + 网页)、真实数据层(持久化跨重启,最终 SQLite)、有状态工作流、agent 必须真的跑起产品、verifier 起真实服务器做端到端执行检查(非 LLM-judge、无浏览器自动化)。

1. **零到一 + 模糊起步**:round-1 是口语化的业务简述("客服用 Excel 记工单,乱糟糟的…你看着办"),agent 必须读 `/workspace/docs`(契约)并向 user 澄清业务决策(SLA 阈值、删除策略、是否登录等)——`user_knowledge` 只在被问时揭示。
2. **需求变更与大幅重构**:M2 加搜索/筛选/指派/严格状态机;M3 **大幅重构**(单脚本 → 可导入包 + SQLite + pytest + SLA/超时逻辑)。
3. **需求反转 + 长期记忆(M4 核心)**:`api.md` 作为 **v1 契约把 DELETE 固定为永久删除**(且不含 `deleted`/`restore`/`include_deleted`)→ M4 **推翻该指令**:改为软删除 + 恢复接口 + 统计。verifier 对最终状态跑全部 4 个里程碑检查,所以"忘了 M1 契约"或"没忘旧删除行为"都会被捕获(硬删除 → 恢复失败 → M4=0)。
4. **真实执行检查**:verifier 自行启动服务(临时端口 + 临时 `TICKET_DB`),用隐藏输入(seeded 事实)走 HTTP 端到端:创建/列表/详情/过滤/状态流转/删除/软删除/恢复/统计/重启持久化/SQLite 魔数/页面存在性;`docs/` 必须与 `ground_truth/` 字节一致。

**里程碑**:
- **R1 `ticket_crud`**:按 api.md 实现服务:健康检查、创建(400 空 title、默认 medium、可选 created_at 回填)、列表、详情、**持久化跨重启**、网页(`云服客服` + 工单区域)。
- **R2 `ticket_workflow`**:q/status/priority/assignee 过滤、指派、严格状态机(逐级 + 重开,跳级 400)、硬删除(删后不在列表)。
- **R3 `ticket_refactor_sla`**:可导入包 `ticket_system/` + **SQLite** + pytest 全绿 + `overdue` 超时逻辑(列表/详情返回)。
- **R4 `ticket_softdelete_report`**:删除策略反转(软删除 + `deleted` 字段 + `restore` + `include_deleted=1`)+ 统计接口(by_status/by_priority/avg_resolution_hours/overdue_count)。

**scorer 要点**:`_start_app` 起真实服务并轮询 `/api/health`;`_stop_app` killpg 清理;临时端口 + 临时 DB 隔离 agent 遗留服务/数据;M1 重启持久化、M3 SQLite 魔数、M4 软删除正反断言 + 删除状态跨重启保持;入口发现跳过不存在的候选文件(§6.9);防篡改:docs 字节一致。

### 4.6 T6 devteam(协同开发工具:CLI + HTML 仪表盘;长上下文 + 澄清 + 需求变更 + 权限反转)

**形态**:空工作区(无预置材料、无 `ground_truth/` —— 测试输入全部在 scorer 内用 seeded-RNG 即时生成)。agent 构建一个**团队协同开发命令行工具** `devteam`(项目/成员/角色 + 迷你 VCS + 日程/概览/HTML 仪表盘 + 质量检查/自动补全),跨 4 个里程碑演进。这是把真实产品草稿(task_1.txt:协同开发工具)落成 headless 可验证任务的一次界定:
- "轻量化IDE"重新界定为**由命令管理的代码工作区**(`projects/<项目>/code/`)+ commit/rollback/history/file-history;
- "用户界面优化"重新界定为 `status` 概览 + 列表命令 `--output-json` + `dashboard <项目>` 生成**自包含 HTML 概览页**(verifier 校验页面文件内容)。

四个考察目标:
1. **长上下文/长程**:工具是有实际意义的应用(多命令 CLI + JSON 状态 + 磁盘快照 + HTML 生成 + ast 代码分析),workspace 跨里程碑持续增长,每轮只给"模拟用户那一条自然消息",agent 必须从环境 + 消息重建上下文。
2. **准确理解需求 / 主动澄清**:round-1 `instruction.md` 刻意**简略**(只说要建项目/管成员/权限/版本控制,细节全不写);agent 必须主动问(命令怎么命名?数据存哪?角色怎么分?权限规则?),user 通过**澄清子循环**回答(`user_knowledge` 只在被问时揭示)。问得不够 → 按错误假设实现 → verifier 判 0。
3. **灵活应对需求变更**:M2 追加迷你 VCS(commit/rollback/history/file-history,协作署名);M3 追加日程 + UI 优化(概览/JSON/HTML);M4 追加质量检查 + 自动补全。每次都是大特性追加,旧命令必须不坏(回归)。
4. **长期记忆 + 遗忘被推翻指令(M4 核心)**:M1 定权限规则("viewer 只读,不能提交";非成员禁入;仅 owner 管成员);M4 **推翻 viewer 只读**("viewer 也能提交,所有成员都可提交")。scorer 对最终状态跑全部 4 个里程碑检查:**M1/M2 的检查只测 owner/member 的权限(不测 viewer 只读),给 M4 反转留空间**;M4 检查专门断言 viewer 能提交 —— agent 若还在执行 M1 的"viewer 只读"→ M4=0;若丢了 M1 的成员管理/非成员禁入 → M1 检查仍判 0。双轴同时捕获"记住 M1"与"遗忘旧规则"。

**里程碑**:
- **R1 `devteam_org`**:`devteam project create/list/remove`、`member add/remove/list --project --role owner|member|viewer`;数据持久化到当前目录 `devteam.json`;操作者 = `DEVTEAM_USER`(默认 root 超管);权限规则(非成员禁入、仅 owner 管成员、viewer 只读)。
- **R2 `devteam_vcs`**:代码工作区 `projects/<项目>/code/`(团队直接写文件);`commit -m`(快照 + 署名)、`history`(新到旧)、`rollback`(恢复文件)、`file-history`(按文件看提交)。
- **R3 `devteam_schedule`**:`event add/list/remove --date`(日程)、`status`(项目概览:项目名/成员数/文件数/提交数/未来 7 天日程)、`dashboard <项目>`(自包含 HTML 到当前目录)、`member list/event list/history` 支持 `--output-json`。
- **R4 `devteam_quality`**:`check <项目>`(语法错误/未定义变量/TODO,输出 `文件:行号: 问题`)、`autocomplete <项目> <前缀>`(收集项目代码里定义的函数/类/变量名);**权限反转**——viewer 也能提交(取代 M1 的"viewer 只读")。

**scorer 要点**:无 `seed/` 无 `ground_truth/` —— 测试输入全部**运行时生成**:项目/成员名从名字池 seeded-RNG 抽样、日程日期相对 `date.today()` 计算、代码文件(注入语法错误/TODO、生成标识符)由 scorer 现场写入;入口发现跳过不存在的候选文件(§6.9 教训);每轮检查在临时 cwd 里驱动真实 CLI、断言 stdout/退出码/磁盘状态;`reward` = 乘积。

### 4.7 复杂度阶梯对照

| 维度 | T0 stats | T1 todo | T2 repofix | T3 pkg | T4 support-bot | T5 ticket-system | T6 devteam |
|---|---|---|---|---|---|---|---|
| 起点 | 空 | 空 | **既有坏代码** | 空 | **预置知识库 + API mock + docs** | **预置业务文档 + 接口契约 + 样例** | 空 |
| 状态性 | 无(每调用独立) | **跨进程持久化** | 有(输入数据) | 有(安装状态) | 有(多文件应用 + 外部 HTTP API) | **有(HTTP 服务 + 数据层 + 状态机)** | **有(JSON 状态 + 磁盘快照 + 跨进程)** |
| 工具链 | stdlib 脚本 | stdlib 脚本 | stdlib 脚本 | **pip/pytest/安装** | stdlib + urllib(HTTP API) | **stdlib http.server + sqlite3** | stdlib(含 ast/html) |
| 每轮新增能力 | 加 flag | 加 flag+持久化语义 | 调试→边界→重构 | 结构→测试→入口 | 问答→API→重构+批量→多语言+转人工 | CRUD→工作流→重构+SQLite+SLA→软删除反转+统计 | 组织→VCS→日程+UI→质量+补全+权限反转 |
| 边界/鲁棒性 | 文件缺失 | 空数据 | **重点** | 词频大小写/标点 | 未知问题(不编造)、404 订单、多语言 | 400/404/409、状态机跳级、重启持久化、SQLite | 非成员禁入、viewer 权限反转、回滚覆盖、语法错误/TODO |
| 自我验证 | 弱 | 弱 | **强(pytest)** | **强(pytest)** | **强(pytest)** | **强(pytest)** | 弱(无 pytest 要求) |
| 澄清/沟通 | 无 | 无 | 无 | 无 | **强(澄清子循环)** | **强(澄清子循环 + 读契约)** | **强(澄清子循环)** |

## 5. 评测标准与指标

1. **主奖励**:`reward = ∏ round_N`(稀疏 0/1),与需求 #5、判别器一致。逐轮 `round_N` 作稠密 RLVR 诊断。
2. **每任务必须验证**:参考解法(全轮)→ reward=1;LastOnly 判别器(只做最后轮)→ reward=0。两个方向都过才算任务合格。
3. **新增(本设计建议,框架缺口)**:**judge-vs-scorer 分歧报告** —— `interactive_transcript.json` 已有 `decisions`(含 satisfied/forced_advance);建议后续在决策日志里补一列 `round_N` 执行分,跑完后输出"user 说满足但执行分 0"与"执行分 1 但 user 说不满足"两类分歧,作为 user-LLM 质量哨兵(§2.4)。
4. **多用户模型稳健性**:对每个新任务抽 1-2 次用小样本第二 user-LLM 跑,对比 reward 分布。
5. **可审计性**:`decisions[].workspace_evidence` 已记录真实 diff;复杂任务(repofix R3)关注 diff 截断是否吞掉关键改动。

## 6. 参考文献

**最相关(设计依据)**
- SWE-Interact(Scale, 2026, arXiv:2606.30573)
- SWE-Together(2026, arXiv:2606.29957)
- τ-bench / τ²-bench(Sierra, arXiv:2406.12045 / arXiv:2506.07982)
- EvoCode-Bench(2026, arXiv:2605.24110)
- SR-Eval(2025, arXiv:2509.18808)
- SWE-bench(ICLR 2024, arXiv:2310.06770)与 SWE-bench Verified(princeton-nlp)
- Ambig-SWE(2025, arXiv:2502.13069)

**任务形态 / 能力分类**
- AgentBench(ICLR 2024, arXiv:2308.03688)
- "Evaluation and Benchmarking of LLM Agents: A Survey"(KDD'25, arXiv:2507.21504)
- MMAU(NAACL 2025 Findings)、CodeIF(ACL 2025 Industry)
- "Exploring Autonomous Agents: Why They Fail"(ASE 2025, arXiv:2508.13143)
- RefactorBench(2025, arXiv:2503.07832)、SWE-EVO(2025, arXiv:2512.18470)
- terminal-bench(harbor-framework/terminal-bench)、CORE-Bench(TMLR 2024, arXiv:2409.11363)
- InterCode(NeurIPS 2023, arXiv:2306.14898)、WebArena(ICLR 2024, arXiv:2307.13854)、AppWorld(ACL 2024, arXiv:2407.18901)、BigCodeBench(2024, arXiv:2406.15877)
- Asuka-Bench(2026)、ClarEval(2026, arXiv:2603.00187)、RECODE-H(ICLR 2026)、CodeAssistBench(NeurIPS 2025)

**user-LLM / judge 可靠性**
- MINT(ICLR 2024, arXiv:2309.10691)
- "Lost in Simulation"(ACL 2026 / arXiv:2601.17087)
- "Mind the Sim2Real Gap in User Simulation"(2026, arXiv:2603.11245)
- "On the Effectiveness of LLM-as-a-Judge for Code Generation"(IEEE TSE 2025)
- "Benchmarking Code Verification Strategies with LLMs-as-a-judge"(ICLR 2026)
- "Building to the Test"(2026, arXiv:2606.28430)、HumanEval+/EvalPlus(NeurIPS 2023, arXiv:2305.01210)
- Persona Policies(2026, arXiv:2605.12894)、RealUserSim(2026, arXiv:2605.20204)

**可验证奖励 / RLVR(警示)**
- SWE-Gym(ICML 2025, arXiv:2412.21139)、SWE-RL(NeurIPS 2025, arXiv:2502.18449)、SWE-smith(NeurIPS 2025, arXiv:2504.21798)

---

## 7. 实现状态

- ✅ **T1 todo-tracker**、**T2 repofix**、**T3 pkg-wordcount** 均已实现(`tasks/benchmark/{todo-tracker,repofix,pkg-wordcount}/`)并**本地验证**:参考解法 → 全轮 1 / reward=1;判别器 → reward=0;Harbor `--print-config` 预检通过;scenario 经 `Scenario` 模型解析 OK;单测 66/66(新增 20)。
- ✅ **T4 support-bot**(`tasks/benchmark/support-bot/`)+ **澄清子循环框架能力**:已实现并本地验证——参考解法 → 全轮 1 / reward=1;部分实现判别器(`FirstTwoClaude`,只做 KB+订单)→ reward=0;Harbor 预检通过;单测 92/92(框架 82 + support-bot scorer 10)。
- ✅ **T5 ticket-system**(`tasks/benchmark/ticket-system/`,产品开发类):已实现并本地验证——参考解法 → 全轮 1 / reward=1;判别器(`PartialTicketClaude`,只做 CRUD+工作流,单脚本 JSON)→ round_1,2=1,round_3,4=0 / reward=0;Harbor 预检通过;单测 103/103(框架 82 + support-bot 10 + ticket-system 11)。
- ⏳ **T4/T5 Novita 端到端**:待跑。重点观察澄清轮真实触发(`decisions[].action=="answer"`)、agent 开发期自己起服务、user-LLM 对需求反转(M4 语言跟随 / M4 软删除)的转述质量、judge-vs-scorer 分歧。
- ✅ **T1 Novita 端到端(07:26 + 07:50 重跑)**:首跑交互链路完美但 verifier 因 **scorer 候选入口 bug** 全判 0(`python3 src/todo.py` 缺失时 rc=2 短路,见 PROJECT_STATE.md §6.9);修 scorer 后重跑 **round_1..4=1,reward=1**。4 轮全 satisfied、无纠正轮;user-LLM 消息忠实且与 verifier 判定**完全一致**(judge-vs-scorer 分歧=0)。
- ⏳ **T2/T3 Novita 端到端**:待跑(每个 1-2 次)。重点观察 user-LLM 判定/转述质量在更复杂任务上是否稳定(文献警示 τ²-bench 47% 模拟器错误;Lost in Simulation ±9pp 用户模型敏感性),记录判定 vs verifier 分歧。

*本文件随任务实现迭代更新;实现进度记录在 `PROJECT_STATE.md`。*
