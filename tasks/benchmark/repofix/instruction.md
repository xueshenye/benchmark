# Fix the sales pipeline

`/workspace` 里有一个销售流水处理工具,按 **类别** 统计销售额。它现在有两处问题,团队测试在 `tests/` 里,当前是失败的。

背景:

- 输入是 CSV,列名为 `date,category,amount`。
- 期望输出:按 `category`(第 2 列)分组、对 `amount`(第 3 列)求和,每行格式 `<category>: <total>`(金额保留两位小数),按类别名排序。例如:

```
electronics: 25.50
books: 10.00
```

- 表头行要跳过;**空 amount 的行应该被跳过,而不是崩溃**。

请修复 `/workspace/pipeline.py`,使:

1. `python3 -m pytest` 全部测试通过;
2. `python3 pipeline.py data/sample.csv` 输出正确的按类别汇总。

改完请自己跑一遍确认。你可以查看 `README.md`、`tests/` 和 `data/sample.csv` 了解预期行为。
