# Multi-turn Agent Benchmark (programming domain)

基于 [Harbor](https://github.com/laude-institute/harbor) 框架构建的编程领域 **Multi-turn Agent Benchmark**。

> **状态:项目初始化中。** 任务内容待需求完整描述后编写。

## 环境

- uv 管理的 venv:`.venv`(Python 3.12.13)
- Harbor CLI:`.venv/bin/harbor`(当前版本 0.20.0)
- 安装/刷新依赖(用 uv,不要用 pip —— 镜像源过期/超时):

  ```bash
  uv sync
  ```

> 本机**不使用 Docker**。Harbor 默认执行 provider 是本地 Docker;任务的实际执行需要 Docker 或云 provider(Daytona/Modal/E2B 等),该决策待定。

## 目录结构

```
proj/
├── tasks/            # 每个子目录是一个 Harbor task
│   └── <task-name>/
│       ├── instruction.md      # 给 agent 的自然语言任务描述
│       ├── task.toml           # 配置/元数据([agent] [verifier] [environment],multi-turn 用 [[steps]])
│       ├── environment/Dockerfile
│       ├── solution/solve.sh
│       └── tests/test.sh       # verifier,必须写 reward 到 /logs/verifier/reward.txt
├── README.md
└── CLAUDE.md
```

Multi-turn(多轮)任务使用 `steps/` 子目录 + `task.toml` 的 `[[steps]]` 配置,每步有独立的 `instruction.md`/`tests/`,可用 `min_reward` 门控提前终止。

## 如何添加一个任务

```bash
cd tasks
../.venv/bin/harbor task init <org>/<task-name>            # 单步任务
../.venv/bin/harbor task init <org>/<task-name> --steps 2  # 多步(multi-turn)任务
```

然后按 Harbor 任务教程编写 `instruction.md`、`task.toml`、`environment/Dockerfile`、`tests/`、`solution/`。参考:
- Harbor 任务教程:`docs/content/docs/tasks/task-tutorial.mdx`
- Multi-step 文档:`docs/content/docs/tasks/multi-step.mdx`

## 运行(待执行方式确定)

```bash
.venv/bin/harbor run -p <task> -a oracle            # 用参考解法验证任务可解
.venv/bin/harbor run -p <task> -a <agent> -m <model>  # 用真实 agent 跑
```
