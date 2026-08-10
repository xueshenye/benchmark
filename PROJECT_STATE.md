# PROJECT_STATE.md — 项目进展与交接文档

> 本文件是**新会话的第一入口**。先读 `CLAUDE.md`(章程/框架参考),再读本文件(当前进展/计划/踩坑)。更新日期:2026-08-10。

## 1. 项目目标与核心设计

在 [Harbor](https://github.com/laude-institute/harbor) 0.20.0 上构建**编程领域 Multi-turn 交互式 Agent Benchmark**。

与普通单轮任务的区别:**"用户"在 agent 完成任务过程中多次介入**,由一个独立 LLM(**user-LLM**)根据 agent 上一轮的实际输出,生成下一轮自然的 user 消息。评测 agent 是否:持续理解上下文、正确更新需求、不遗忘旧约束(回归)、最终完成编程任务。reward 可用于 RLVR。

**每轮 instruction 只包含模拟用户那一条自然消息(不重放历史)** —— agent 必须从环境(已有代码)+ 新消息重建上下文。

**核心判别(需求 #5)**:`reward = 各轮得分乘积`(稀疏 0/1)。只完成最后一轮的 agent → 早期轮 = 0 → reward=0;完整多轮的 agent → reward=1。

**reward 协议(与需求原文的偏差)**:需求写的是 `/logs/verifier/rewards.txt`,但 Harbor 0.20.0 verifier 只读 `reward.txt`(标量)或 `reward.json`(扁平 dict str→number)。本项目用 **`/logs/verifier/reward.json`**:`{"round_1":0|1, ..., "reward":<乘积>}`。多键 reward 是产品,documentation 里记录的 `rewards.json`/`rewards.txt` 均不存在,勿用。

## 2. 当前架构(MVP = 方案 A)

```
harbor run -e novita --env-file .env -p <task> -a benchmark.interactive_agent:InteractiveUserClaude -m <model>
  │ agent.run() [宿主进程,PYTHONPATH=. 导入 benchmark/]
  │   读容器内 scenario.json(exec cat /workspace/scenario.json)
  │   Round 1: instruction = instruction.md(初始任务)→ claude --print 执行 → 捕获输出
  │   Round 2..N: user-LLM(LiteLLM)依 persona + 该轮 user_intent/requirement + 上轮 agent 输出 → 自然 user 消息
  │             → instruction = 仅这条消息 → claude --print 执行 → 捕获输出
  │   写 interactive_transcript.json 工件
  └─ 结束后 → verifier(tests/scorer.py,容器内)对最终状态逐轮累计检查 → 写 reward.json
```

- 轮次共享同一沙箱,前几轮代码保留,后续轮次在其上修改。
- agent 注册:`-a benchmark.interactive_agent:InteractiveUserClaude`(Harbor import-path 工厂,已验证可实例化)。
- 文件布局:`benchmark/{interactive_agent,user_simulator,scenario,prompt_templates}.py`;`tasks/benchmark/multi-round-cli-demo/`;`tests/`(无 Docker 单元测试)。

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

**scenario.json(environment/,会烘焙进容器 /workspace/scenario.json,verifier 与 agent 都读它):**
```json
{
  "num_rounds": 3,
  "user_persona": "一位数据产品经理,说话简洁直接,很在意 CLI 的向后兼容和易用性。",
  "rounds": [
    {"index":1, "requirement":"stats CLI 能读取单个 CSV 文件并打印 count/mean/min/max 统计摘要",
     "user_intent":"让 agent 从零实现一个处理 CSV 的 stats 命令行工具", "test_id":"stats_basic"},
    {"index":2, "requirement":"新增 --output-json 选项:JSON 输出必须是 JSON 数组,每个元素包含 count/mean/min/max 四个字段;即使只有一个输入文件,也输出长度为 1 的数组;默认仍为纯文本输出,不带该选项时行为不变",
     "user_intent":"在现有 stats 工具上加一个 --output-json 选项,并要求 JSON 输出总是数组格式(单文件也输出 [{...}] 数组)", "test_id":"stats_json"},
    {"index":3, "requirement":"支持多个输入文件参数,对每个文件分别输出统计;纯文本模式每个文件一行;JSON 模式输出数组,每个元素对应一个文件;保留单文件、纯文本与 JSON 输出等已有行为(单文件 JSON 仍为长度 1 的数组)",
     "user_intent":"让 stats 支持一次处理多个文件,且不能破坏已有的功能,JSON 输出保持数组格式", "test_id":"stats_multi"}
  ]
}
```

**user-LLM 提示词**(`benchmark/prompt_templates.py` 的 `build_user_message_prompt`):给 LLM persona + `user_intent`(作为"你本轮要说出的话")+ `requirement`("ground truth,仅供把握,不要逐字照抄")+ 到目前为止的对话历史 + agent 上一轮实际输出;要求像真实用户口语化、每轮必须让实现发生改变、只输出要说的话。**注意:`user_intent` 是 user-LLM 主要转述的内容,ground-truth 格式细节必须同时写进 `user_intent` 才可靠传达**(本轮踩坑,见 §6)。

**verifier**(`tests/scorer.py`):`CHECKERS` 按 `test_id` 映射检查函数;对**最终** workspace 跑 3 轮检查 → 写 `/logs/verifier/reward.json`。`check_stats_json` 要求 `--output-json <单文件>` 输出 `[{count,mean,min,max}]`(list-of-one);`check_stats_multi` 要求多文件 JSON 为数组(长度=文件数)。

**参考解法**(`solution/solve.sh`):`--output-json` 永远 `json.dumps(results)`(数组,单文件也 `[{...}]`);纯文本每行 `file: count=...`。本地验证 3 轮全 1。

## 5. 验证结果(截至 2026-08-10)

| 验证 | 结果 |
|---|---|
| 单元测试 `.venv/bin/python -m pytest tests/` | ✅ 12/12 |
| 参考解法 → scorer(本地,无容器) | ✅ round_1,2,3=1,reward=1 |
| "只完成后面轮次"实现 → scorer(本地) | ✅ round_1=0,2=1,3=1 → reward=0(判别器有效) |
| 端到端 run #1(14:00) | ❌ errored — `AgentAuthenticationError`(沙箱 claude "Not logged in")→ 修复(§6) |
| 端到端 run #2(14:08) | ✅ 完成,0 error;**round_1=1, round_2=0, round_3=1 → reward=0**。诊断:round_2 ground-truth 格式歧义(§6)→ 修 scenario.json |
| 端到端 run #3(14:23) | ✅ **round_1,2,3=1 → reward=1**。显式格式规格修复生效:Kimi 把"总是数组"完整转述进 round_2/round_3 自然消息,agent 按数组实现,三轮全过 |

run #2 证明整条流水线(Design A + Novita + DeepSeek 后端 + Kimi user-LLM)在真实部署下工作;round_2=0 不是 harness bug,是任务规格问题。run #3 证明 **ground-truth 显式化 → user-LLM 忠实转述 → agent 正确实现**的链路成立:把格式/约束细节写进 `requirement`+`user_intent`,Kimi 会原样传达(甚至补充理由),agent 据此实现。

## 6. 踩坑记录(重要)

1. **agent 认证**:沙箱 claude 必须有自己的 LLM 后端(host OAuth 不继承)。→ `.env` 加 `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`(DeepSeek 代理)。缺失时 claude `Not logged in` → trial error → verifier 全 0。
2. **LiteLLM 模型前缀**:`USER_LLM_MODEL` 必须带已知 provider 前缀(`openai/...`);裸 `moonshotai/kimi-k3` 使 LiteLLM 解析崩溃。
3. **Novita 余额**:LLM/沙箱都按账号计费;没余额 → 403 NOT_ENOUGH_BALANCE。GET /models、GET /templates 免费。
4. **ground-truth 与自然指令一致性**(本轮关键教训):scorer 必须与"user-LLM 转述后的自然消息能合理推导出的行为"一致。run #2 中 round_2 的 JSON 容器格式(数组 vs 对象)没写进 requirement/user_intent,agent 选扁平对象而被 scorer(要求 list-of-one)判 0。**修法**:把格式细节写进 `requirement` **和** `user_intent`(§4)。
5. **`.env` 安全**:真实 key 在 `.env`,已 git-ignore;`.env.example` 是模板。

## 7. 未完成工作

- [x] **run #3** → round_1,2,3=1,reward=1(user-LLM 转述质量已确认良好)
- [ ] **user-LLM 转述质量**:run #3 一次通过,但只在 Kimi/K3 + 这一任务上验证过;更多任务/轮次上继续观察,必要时调 `prompt_templates.py`/persona
- [x] **提交所有改动** → `c1f5c41`(.gitignore、CLAUDE.md、README.md、pyproject.toml、uv.lock、scenario.json、.env.example、PROJECT_STATE.md;`.env` 已 git-ignore 不提交)
- [ ] **真实端到端验证"只完成最后一轮"判别器**(在 Novita 上故意跑一个只做 round_3 的 agent,确认 reward=0;目前只在本地验证过)
- [ ] 待用户补充的真实 benchmark 任务内容(当前只有 demo 任务)
- [ ] Design B(见 §8)

## 8. 未来计划(含 MVP 后改进)

**短期(当前轮)**:run #3 已 reward=1 → 提交所有改动;然后在 Novita 上做一次"只完成最后一轮"的判别验证(故意部分实现),确认真实部署下 reward=0 也能被正确判出。

**Design B(原生 multi-step 重构,已规划,未开始)**:把轮次从"单 trial 内 agent.run() 循环"(方案 A)改为 Harbor 原生 multi-step —— **每步 = 一轮**,一个 runner/hook 在步间读上一步的 agent 轨迹 → 调 user-LLM → 动态生成下一步 instruction(如重写 `steps/step-N/instruction.md` 懒加载)。收益:原生 per-step reward + `min_reward` 门控 + per-step 轨迹,RLVR 更友好。需要执行 provider 支持原生 multi-step 行为,且要在 Novita 上验证。

**Benchmark 质量改进方向**:
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
