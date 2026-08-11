# Multi-turn Agent Benchmark (programming domain)

基于 [Harbor](https://github.com/laude-institute/harbor) 框架构建的编程领域 **Multi-turn 交互式 Agent Benchmark**。

> **状态:已实现 Design A+(动态里程碑 + 纠正轮)。** 交互式多轮 agent 已实现并通过单元测试;端到端运行走 **Novita** 云执行 provider(已配置,待填入 API key)。

## 核心思想

与普通单轮任务不同:**"用户" 会在 agent 完成任务过程中多次介入**(由一个独立 LLM 根据 agent 的实际输出生成下一轮 user prompt),评测 agent 是否:

- 在多轮对话中持续理解上下文
- 正确更新需求
- 避免遗忘旧约束(回归)
- 最终完成编程任务

**每轮 instruction 只包含模拟用户那一条自然消息**(不重放历史)—— agent 必须从环境(已有代码)+ 新消息重建上下文,严格考验记忆与约束保持。

## 架构(Design A+:自定义 agent,单 trial,动态里程碑)

```
harbor run -p <task> -a benchmark.interactive_agent:InteractiveUserClaude ...
  │ agent.run() [宿主进程]
  │   读容器内 scenario.json(exec cat)
  │   控制器 TurnController 驱动(轮次动态):
  │   Round 1: instruction = instruction.md(初始任务,里程碑 1)→ claude --print → 捕获输出
  │   每轮后: 抓取 /workspace 快照并 diff(上轮 vs 本轮)→ 真实改动证据
  │   后续轮: user-LLM 据「上轮输出 + 工作区 diff」判定 → 严格 JSON {"satisfied": bool, "message": str}
  │       satisfied=true  → 推进下一里程碑(message = 下一需求)
  │       satisfied=false → 纠正轮,留在当前里程碑(纠正次数 ≤ max_corrections)
  │      纠正耗尽       → 强制推进(该里程碑由 verifier 判 0)
  │     → instruction = 仅 message → claude --print 执行 → 捕获输出
  │   写 interactive_transcript.json 工件(scenario + decisions[含 workspace_evidence] + transcript)
  └─ 结束后 → verifier(tests/scorer.py,容器内)
      读 scenario.json → 对最终状态逐里程碑累计检查(该里程碑需求 + 之前所有里程碑回归)
      写 /logs/verifier/reward.json {round_1..N, reward=各里程碑乘积}
```

- 轮次 = 共享同一容器,前几轮的代码保留,后续轮次在其上修改;**总轮次动态**(`max_rounds` 硬上限,`max_corrections` 每里程碑纠正预算)。
- **区分"只完成最后一轮"与"完整多轮"**:per-round 键逐里程碑检查最终代码是否仍满足该里程碑需求。示例:`round_1=0,round_2=1,round_3=1 → reward=0`;完整实现 → `reward=1`。
- **RLVR**:`reward`(各里程碑乘积,稀疏 0/1)作为标量信号;`round_1..N` 键为稠密诊断;`reward.json` 直接进入 `VerifierResult.rewards`。纠正轮只影响交互过程,不改 reward 键结构。

## 目录结构

```
proj/
├── benchmark/                        # 基准框架代码(需在 PYTHONPATH 上)
│   ├── interactive_agent.py          # InteractiveUserClaude:循环 + claude-code 驱动
│   ├── controller.py                 # TurnController:动态里程碑状态机(advance/纠正/强制推进)
│   ├── user_simulator.py             # UserSimulator:LiteLLM 封装,判定+生成 user 消息
│   ├── scenario.py                   # Milestone/Scenario 模型(schema 校验)
│   └── prompt_templates.py           # user-LLM 提示词(判定 prompt + 消息 prompt)
├── tasks/benchmark/multi-round-cli-demo/   # 示例任务(3 个里程碑)
│   ├── instruction.md                # 初始任务(milestone 1)
│   ├── task.toml
│   ├── environment/{Dockerfile, scenario.json}   # scenario.json 进容器 /workspace/
│   ├── solution/solve.sh             # 参考解法(全部 3 个里程碑)
│   └── tests/{test.sh, scorer.py}    # 逐里程碑累计计分 → reward.json
├── tests/                            # 无 Docker 单元测试(含 controller/scorer)
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

## Design B(原生 multi-step,实验性)

把每轮改为 Harbor **原生 step**(每步 = 一轮),步间 runner 读上一步轨迹 → user-LLM → 动态生成下一步指令。收益:原生 per-step reward + per-step 轨迹,RLVR 更友好。

```
harbor run -e novita --env-file .env \
  -p tasks/benchmark/multi-round-cli-demo-multistep \
  -a benchmark.interactive_step_agent:InteractiveStepClaude -m <model> \
  --plugin benchmark.design_b_plugin:DesignBPlugin
```

- `DesignBPlugin.on_job_start` 在 trial 创建前 monkeypatch `MultiStepTrial` → `InteractiveMultiStepTrial`(零 Harbor 代码改动)。
- `InteractiveMultiStepTrial` 每步后调用 `StepDriver`(user-LLM 判定满意/纠正/强制推进),经包装的 `Task.step_instruction` 注入下一步指令(不改仓库)。
- **reward = `multi_step_reward_strategy="final"`**:最终 reward = 最后一步的全里程碑乘积,与 Design A+ 判别语义一致;每步 reward.json 保留在 `trial_dir/steps/{name}/verifier/` 作稠密诊断。`min_reward` 故意不设(与纠正轮冲突)。
- **回退**:Design B 纯增量——删除 `benchmark/{step_driver,multi_step_trial,interactive_step_agent,design_b_plugin}.py`、`tests/test_{step_driver,interactive_step_agent}.py`、`tasks/benchmark/multi-round-cli-demo-multistep/`,A+ 原样可用。
