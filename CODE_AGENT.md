# Code Agent 项目指令

## 项目形态

这个仓库是一个 Python CLI 编码 agent，灵感来自终端优先的 agent 工具。实现应保持小而清晰、可测试、对模型服务商保持中立。优先通过现有的 `llm.base_url`、`llm.api_key` 和 `llm.model_name` 配置接入 OpenAI-compatible API。

## 本地规则

- 除非用户明确要求，不要移除用户本地 `config.yaml` 中硬编码的 API key。
- 长期记忆必须写入项目内的 Markdown 文件，目前是 `.code_agent_memory.md`，并且内容要有界，不能无限增长。
- 项目本地路径应从 `config.yaml` 所在目录解析，不要依赖启动进程时的任意当前目录。
- 诊断信息要清晰，不要用隐藏重试掩盖问题。服务商/API 问题应引导用户使用 `/doctor`、`/models` 或 `/check-api`。
- shell、file、project 工具必须限制在各自配置的 workspace root 内。只有显式可信的测试配置才能允许绝对路径越界。

## 质量要求

- 行为变更后运行 `ruff check src tests`、`mypy src/code_agent` 和 `pytest -q`。
- 针对 CLI helper、服务商诊断、prompt 构造和 workspace 安全边界添加聚焦测试。
- 除非能直接降低风险或改善用户可见工作流，不要做大范围重构。
