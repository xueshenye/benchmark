# Todo CLI

在 `/workspace` 中创建一个名为 `todo` 的命令行待办工具(入口脚本 `/workspace/src/todo.py`,或 `/workspace/todo.py`,确保命令 `todo add "..."` 可以直接运行)。

用法:
```
todo add "<任务描述>"
todo list
todo done <id>
todo
```

- `todo add "<任务描述>"` 新增一个待办任务,自动分配一个从 1 开始递增的整数 id。
- `todo list` 逐行打印所有**未完成**的任务,每行格式为 `<id>: <描述>`。
- `todo done <id>` 把指定 id 的任务标记为完成。
- `todo` 不带参数时打印一段简短的用法说明。

要求:
- 用 Python 实现,仅用标准库。
- **数据持久化**:任务数据保存在**当前目录下的 `todos.json` 文件**中。多次调用(例如先 `todo add`,再另开一个终端 `todo list`)之间状态要保持一致——我关掉终端再打开,任务还在。
- 在 `/workspace` 下给出实现文件,并确保可以直接运行。
