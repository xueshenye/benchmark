# wordcount Python package

在 `/workspace` 中创建一个可安装的 Python 包 `wordcount`,用于统计一段文本中每个词的出现次数。

要求:

- 提供一个公开函数 `count(text)`,返回 `{词: 次数}` 字典。
  - **分词规则**:按字母数字 token 切分(忽略标点与空白),例如 `"Hello, World! hello"` → token 为 `hello`、`world`、`hello`。
  - **统一转小写**处理:例如 `"Hello"` 和 `"hello"` 视为同一个词。
  - 空文本返回空字典 `{}`。
- 包要能被导入:`cd /workspace && python3 -c "import wordcount; print(wordcount.count('Hello, World! hello'))"` 应输出 `{'hello': 2, 'world': 1}`。
- 工作区根目录要有 `pyproject.toml`,包含包的基本元数据(name/version 等),保证这个包可以被 `pip install`。

建议的包结构:
```
/workspace/
  pyproject.toml
  wordcount/
    __init__.py     # 导出 count
    core.py         # 实现 count
```

请给出完整实现,并确保从 `/workspace` 导入可用。
