# T6 devteam — 团队协同开发工具(任务设计与验收文档)

> 本文档是 devteam 任务的**本地测试文档**:任务作者/演示者照着它读取每个里程碑的
> 需求与评价标准,在跑 demo 或复盘交互时判断 agent 是否达标。设计依据见
> [`docs/task-suite-design.md`](task-suite-design.md) §4.6;框架使用见 `PROJECT_STATE.md`。

## 0. 任务一句话

让 agent 为一个小型软件团队构建一个**协同开发命令行工具** `devteam`(项目/成员/角色 +
迷你 VCS + 日程/概览/HTML 仪表盘 + 质量检查/自动补全),跨 4 个里程碑由"模拟用户"逐轮提出需求演进。

考察四项能力:①长上下文(复杂真实应用,每里程碑可多轮交互)②准确理解需求(用户 prompt 简略,
agent 须反问、用户据 `user_knowledge` 回答)③灵活应对需求变动(追加/细节修改)④长期记忆
(最后里程碑仍遵循 M1 指令、并遗忘被推翻的"viewer 只读"规则)。

## 1. 用户画像与轮次预算

- **user_persona**:团队技术负责人老王,熟悉命令行,说话简短口语化,很在意"真实能用"——
  数据不能丢、每次新需求不破坏已有功能;需求先讲大概,细节让 agent 问。
- **预算**:`max_rounds=12`、`max_corrections=1`、`max_clarifications=3`。

## 2. 全局数据模型 / 权限模型(贯穿)

- 状态在**当前目录 `devteam.json`**(跨进程持久化)。
- 每个项目代码在 **`projects/<项目名>/code/`**(团队直接写文件,devteam 只管版本控制);
  提交快照在 **`projects/<项目名>/.snapshots/<提交id>/`**。
- 操作者 = 环境变量 **`DEVTEAM_USER`**(未设置默认 `root`,root 对一切项目有全部权限)。
- 权限规则(随里程碑演进的最终态):
  - **非成员禁入**:非项目成员对项目任何操作 → 报错 + 非零退出(永不反转,各轮都测)。
  - **仅 owner 管成员**:member add/remove、project remove 仅 owner(永不反转,M1 测)。
  - **viewer 可提交**(M4 反转后的最终态;M1 定的是"viewer 只读",M4 推翻)。

## 3. 各里程碑:需求 + 评价标准(verifier)+ 手动判断要点

> 需求全文见 `environment/scenario.json`(requirement 为 ground truth,user_intent 为自然消息种子)。
> 下列为**验收要点**——verifier(scorer)在最终工作区上逐一检查,手动 demo 时按此判断。

### M1 `devteam_org` — 项目 + 成员 + 角色权限

**需求要点**:`devteam project create/list/remove`、`member add/remove/list --project --role
owner|member|viewer`;数据存当前目录 `devteam.json`;操作者 = `DEVTEAM_USER`;权限规则如上。

**verifier 检查**(`check_devteam_org`):建项目 → `devteam.json` 出现;新进程 `project list`
仍见该项目;加 member/viewer 后 `member list` 输出 `姓名: 角色`(含 owner);角色跨进程保留;
非成员 `member list` → 非零;member 尝试 `member add` → 非零;owner 可 `member remove`。
**不检查** viewer 是否只读(留给 M4 反转)。

**手动判断要点**:① `devteam.json` 在 cwd 且可读 ② `member list` 显示 `alice: owner` 等
③ 非成员/普通 member 越权操作被拒(非零码)。

### M2 `devteam_vcs` — 迷你 VCS + 协作署名

**需求要点**:代码在 `projects/<项目>/code/`;`commit <项目> -m <消息>` 快照 + 署名(DEVTEAM_USER);
`history <项目>` 新到旧 `id 提交者 时间 消息`;`rollback <项目> <id>` 覆盖恢复 code/;
`file-history <项目> <文件>` 列出含该文件的提交。权限:成员可提交;非成员禁入。

**verifier 检查**(`check_devteam_vcs`):写入 2 个代码文件 → commit → history 含消息与提交者;
改文件再 commit → history 首行为最新;rollback 到首个提交 → 文件内容还原;file-history 含两次提交;
非成员 commit → 非零;新进程 history 仍在。

**手动判断要点**:① commit 后 `history` 能看见提交者和消息 ② rollback 后文件内容真的变回
③ 非成员 commit 被拒。

### M3 `devteam_schedule` — 日程 + UI(status/--output-json/dashboard)

**需求要点**:`event add/list/remove --date <YYYY-MM-DD> [--member]`;`status <项目>` 概览含
项目名/成员数/文件数/提交数/未来 7 天日程;`dashboard <项目>` 生成自包含 `dashboard-<项目名>.html`
到当前目录(含项目名/成员/日程);`member list`/`event list`/`history` 支持 `--output-json`。

**verifier 检查**(`check_devteam_schedule`):日程按日期升序列出、`--date` 过滤、`event remove`;
`status` 含项目名 + 未来 7 天日程的日期与标题;`dashboard-<项目名>.html` 存在且含项目名/成员/日程;
三个 `--output-json` 返回正确键的数组;commit 回归可用。

**手动判断要点**:① `status` 一屏看到项目/成员/文件/提交数 + 近 7 天日程 ② 打开
`dashboard-<项目名>.html` 看到项目名、成员名单、日程 ③ 列表命令带 `--output-json` 是 JSON 数组。

### M4 `devteam_quality` — 质量检查 + 自动补全 + 权限反转

**需求要点**:`check <项目>` 扫 code/ 下 `.py`,输出 `文件:行号: 问题描述`(语法错误/未定义变量/
TODO),无问题不输出、退出码 0;`autocomplete <项目> <前缀>` 输出前缀匹配的函数/类/变量名(每行一个,字母序);
**权限反转**:viewer 也能提交(取代 M1 的"viewer 只读",所有成员都可提交;非成员禁入、仅 owner 管成员不变)。

**verifier 检查**(`check_devteam_quality`):注入一个语法错误文件(`def broken(:`)+ 一个 `# TODO:` 文件
+ 一个定义标识符的文件 → `check` 输出匹配 `文件:\d+` 且含 "TODO";`autocomplete` 返回该标识符;
**`DEVTEAM_USER=<viewer>` commit 成功**且 history 含 viewer 署名;非成员仍被拒、member list 正常。

**手动判断要点(记忆/遗忘双轴)**:① `check` 真能找到注入的问题并给出 `文件:行号` ② `autocomplete`
能给前缀提示 ③ **用 viewer 身份 commit 必须成功**(agent 若还执行 M1 的"viewer 只读"→ 此轮 0)
④ 但 M1 的项目/成员管理、非成员禁入仍然工作(没"忘掉"M1)。

## 4. 真人扮演用户模式(手动跑 demo)

不依赖 user-LLM,任务作者亲手扮演用户跑一遍交互(agent 仍需 `ANTHROPIC_*` 后端):

```bash
USER_SIMULATOR=manual .venv/bin/harbor run -e novita --env-file .env \
    -p tasks/benchmark/devteam -a benchmark.interactive_agent:InteractiveUserClaude \
    -m deepseek-v4-flash
```

每轮终端会打印:**当前里程碑的 user_intent / requirement(评价标准)/ user_knowledge** +
agent 实际输出 + 工作区 diff。然后输入下一条用户消息,三种简写(或严格 JSON):

```
s <消息>     满意,推进到下一里程碑(消息 = 下一需求/收尾)
c <消息>     不满意,给出纠正(留在当前里程碑)
a <消息>     agent 在提问澄清,你回答它(留在当前里程碑;回答内容只能来自 user_knowledge)
```

复盘用 `jobs/<timestamp>/agent/interactive_transcript.json`(transcript + decisions,含 workspace_evidence)。

## 5. 验证协议

| 验证 | 命令 | 期望 |
|---|---|---|
| 单测(devteam scorer + manual_user) | `.venv/bin/python -m pytest tests/test_devteam_scorer.py tests/test_manual_user.py` | 全过 |
| 参考解本地 | `sed 's\|/workspace\|<tmpws>\|g' solution/solve.sh \| bash` 后跑 `tests/scorer.py --base-dir <tmpws> --scenario environment/scenario.json --reward-out <out>` | `round_1..4=1, reward=1` |
| 判别器本地 | 用 `benchmark/partial_devteam.py:_PARTIAL`(仅 M1+M2,viewer 仍只读)写进临时 ws 再跑 scorer | `round_1=1, round_2=1, round_3=0, round_4=0, reward=0` |
| Harbor 预检 | `harbor run -p tasks/benchmark/devteam -e novita --print-config` | 通过 |
| 端到端(需 .env 凭证) | `harbor run -e novita --env-file .env -p tasks/benchmark/devteam -a benchmark.interactive_agent:InteractiveUserClaude -m deepseek-v4-flash` | 观察澄清轮 + M4 viewer 反转转述 vs verifier 评分(judge-vs-scorer 分歧) |
| 判别器端到端 | `-a benchmark.partial_devteam:PartialDevteamClaude` | `reward=0` |
| 多模型对比(agent-only) | `./benchmark/run_model_compare.sh`(默认 flash / glm-5.2 / kimi-k3 / **kimi-k2.5 弱模型校准**;DeepSeek + Novita 双后端,user-LLM 固定) | 逐模型 reward/逐轮/轮数/澄清/纠正/force/判分分歧/时长/费用;弱模型掉分 → 难度下界 |
| 长沙箱观察跑 | 加 `--plugin benchmark.debug_long_sandbox_plugin:LongSandboxPlugin`(`NOVITA_SANDBOX_TIMEOUT=<秒>` 可配,默认 2h;1h 沙箱只够 ~6 轮) | 复杂任务跑完 + transcript 同步 |
| **Reward 模式** | `--ve REWARD_MODE=dense`(默认,每轮连续 0-1 部分分)/ `--ve REWARD_MODE=binary`(旧 0/1) | dense 给 RLVR 部分分信号;binary 复现旧判别语义。scorer 读 verifier 环境变量 |

> **难度校准结论(2026-08-12)**:最小可用模型(flash)在硬化前 reward=1,任务对"模型对比"分辨率不足。硬化后(见 §2/§3 与 PROJECT_STATE §5):M4 反转作用域收紧(viewer 可 commit 但 event add/remove 仍仅 owner/member)、`check` 精度(干净零输出/字符串 TODO 不误报/未定义需真 AST)、status 精确计数、隐藏边界用例。已验证:参考解仍 1、判别器仍 0、**"viewer 可管日程"偷懒实现 → round_4=0**(旧 scorer 会给 1)。DeepSeek 后端仅 2 个 distinct agent 模型,`claude-sonnet-5` 静默别名成 flash;Novita Anthropic 端点是第 3+ 模型来源(但非全部模型支持 anthropic)。
