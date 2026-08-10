# Multi-turn Agent Benchmark (programming domain)

基于 [Harbor](https://github.com/laude-institute/harbor) 框架构建的编程领域 **Multi-turn 交互式 Agent Benchmark**。

> **状态:已实现 MVP(方案 A)。** 交互式多轮 agent 已实现并通过单元测试;端到端运行走 **Novita** 云执行 provider(已配置,待填入 API key)。

## 核心思想

与普通单轮任务不同:**"用户" 会在 agent 完成任务过程中多次介入**(由一个独立 LLM 根据 agent 的实际输出生成下一轮 user prompt),评测 agent 是否:

- 在多轮对话中持续理解上下文
- 正确更新需求
- 避免遗忘旧约束(回归)
- 最终完成编程任务

**每轮 instruction 只包含模拟用户那一条自然消息**(不重放历史)—— agent 必须从环境(已有代码)+ 新消息重建上下文,严格考验记忆与约束保持。

## 架构(MVP,方案 A:自定义 agent,单 trial)

```
harbor run -p <task> -a benchmark.interactive_agent:InteractiveUserClaude ...
  │ agent.run() [宿主进程]
  │   读容器内 scenario.json(exec cat)
  │   Round 1: instruction = instruction.md(初始任务)→ claude --print 执行 → 捕获输出
  │   Round 2..N: user-LLM(LiteLLM)依 persona+该轮需求+上轮 agent 实际输出 → 自然 user 消息
  │             → instruction = 仅这条消息 → claude --print 执行 → 捕获输出
  │   写 interactive_transcript.json 工件
  └─ 结束后 → verifier(tests/scorer.py,容器内)
      读 scenario.json → 对最终状态逐轮累计检查(该轮需求 + 之前所有轮回归)
      写 /logs/verifier/reward.json {round_1..N, reward=各轮乘积}
```

- 轮次 = 共享同一容器,前几轮的代码保留,后续轮次在其上修改。
- **区分"只完成最后一轮"与"完整多轮"**:per-round 键逐轮检查最终代码是否仍满足该轮需求。示例(参考 `/tmp` 验证):`round_1=0,round_2=1,round_3=1 → reward=0`;完整实现 → `reward=1`。
- **RLVR**:`reward`(各轮乘积,稀疏 0/1)作为标量信号;`round_1..N` 键为稠密诊断;`reward.json` 直接进入 `VerifierResult.rewards`。

## 目录结构

```
proj/
├── benchmark/                        # 基准框架代码(需在 PYTHONPATH 上)
│   ├── interactive_agent.py          # InteractiveUserClaude:轮次循环 + claude-code 驱动
│   ├── user_simulator.py             # UserSimulator:LiteLLM 封装,反应式生成 user 消息
│   ├── scenario.py                   # 多轮场景模型(schema 校验)
│   └── prompt_templates.py           # user-LLM 提示词
├── tasks/benchmark/multi-round-cli-demo/   # 示例任务(3 轮)
│   ├── instruction.md                # 初始任务(round 1)
│   ├── task.toml
│   ├── environment/{Dockerfile, scenario.json}   # scenario.json 进容器 /workspace/
│   ├── solution/solve.sh             # 参考解法(全部 3 轮)
│   └── tests/{test.sh, scorer.py}    # 逐轮累计计分 → reward.json
├── tests/                            # 无 Docker 单元测试
├── .env.example                      # 运行凭证模板(NOVITA_API_KEY / USER_LLM_*)
└── CLAUDE.md
```

## 环境

- uv 管理的 venv:`.venv`(Python 3.12.13);Harbor CLI:`.venv/bin/harbor`(0.20.0)
- 安装/刷新依赖(用 uv,不要用 pip —— 镜像源过期/超时):

  ```bash
  uv sync
  ```

- 测试:`.venv/bin/python -m pytest tests/`

> 本机 Docker daemon 虽在运行,但当前用户无 root/socket 权限且无 sudo → **端到端运行走 Novita 云沙箱**(`-e novita`);任务编写与单元测试不受影响。

## 运行(执行 provider = Novita)

Novita 会把任务的 `environment/Dockerfile` 在云端构建成模板并启动沙箱(支持 FROM/RUN/COPY/ADD/WORKDIR/USER/ENV/CMD)。模板按 `environment_name + env_hash + key 后缀` 缓存复用;沙箱最长 1 小时(超时自动销毁),按量计费。

```bash
cp .env.example .env   # 填入 NOVITA_API_KEY + USER_LLM_*

# 完整多轮交互(agent 为 claude-code,user 为另一个 LLM)
PYTHONPATH=. .venv/bin/harbor run --env-file .env -e novita \
  -p tasks/benchmark/multi-round-cli-demo \
  -a benchmark.interactive_agent:InteractiveUserClaude -m <claude-model>

# 用参考解法验证任务可解(仅跑 verifier 路径)
.venv/bin/harbor run --env-file .env -e novita \
  -p tasks/benchmark/multi-round-cli-demo -a oracle

# 不花钱的配置预检(解析 JobConfig,不发 API 请求)
.venv/bin/harbor run -p tasks/benchmark/multi-round-cli-demo -e novita --print-config
```

需要的凭证:
- `NOVITA_API_KEY` — [novita.ai](https://novita.ai) Key Management 创建(`sk_` 开头)
- `USER_LLM_MODEL` / `USER_LLM_API_BASE` / `USER_LLM_API_KEY` — 模拟用户 LLM(Anthropic 兼容端点;Novita 也提供 `/v3/anthropic`,可同时用于执行 + user-LLM)
- `-m <claude-model>` — agent 用的模型名

> SDK:`uv add novita-sandbox` 已装(Harbor 对 cloud provider 懒加载,基础包不含)。Novita 域默认 `us-phx-1.sandbox.novita.ai`,可用 `NOVITA_DOMAIN`/`NOVITA_API_URL` 覆盖。

## 重要说明:reward 协议偏差

需求原写 "test.sh 将最终得分写入 `/logs/verifier/rewards.txt`"。经核对 Harbor 0.20.0 源码,verifier **只读取** `/logs/verifier/reward.txt`(标量)或 `/logs/verifier/reward.json`(扁平 dict:str→number);`rewards.txt`/`rewards.json`(复数)在代码中不存在(官方模板注释里的 `rewards.json` 是笔误)。因此本项目用 **`/logs/verifier/reward.json`**(多键,语义即"多个 rewards")写入 per-round 键 + 最终 `reward`。

## 自定义 agent 注册

通过 Harbor 的 import-path 注册(`-a "module:ClassName"`)。已验证 `AgentFactory.create_agent_from_import_path("benchmark.interactive_agent:InteractiveUserClaude", ...)` 可实例化。
