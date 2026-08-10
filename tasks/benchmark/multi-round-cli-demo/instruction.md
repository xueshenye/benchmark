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
