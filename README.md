# Code Agent

一个面向中文开发者的 CLI 编码 Agent。它使用 LangChain/LangGraph 构建，兼容 OpenAI-compatible API，可在项目内读取配置、执行工具、维护记忆、运行工作流，并提供普通命令行模式和窗口式终端 UI。

## 功能特性

- **交互式编码对话**：用自然语言让 agent 阅读、分析、修改和验证代码。
- **文件与代码编辑**：读取、写入、精确替换、插入、删除代码或应用 diff。
- **Shell 与 Git 工具**：运行测试、构建、包管理命令，并查看 git 状态、diff、log、提交等。
- **项目分析**：查看项目结构、文件摘要和依赖信息。
- **工具搜索与懒加载**：模型默认只看到相关工具，必要时用 `tool_search` 发现并激活更多能力，减少上下文噪声。
- **语义代码导航**：`lsp_tool` 支持符号、定义、引用和基础 Python 语法诊断。
- **中文 CLI 交互**：欢迎页、帮助、命令说明、工具描述和权限提示面向中文用户。
- **命令补全**：支持 slash 命令联想、技能名补全、workflow 名补全、工具名补全和文件路径补全。
- **历史与参数提示**：支持历史搜索，并在输入时显示命令参数提示。
- **窗口模式**：全屏终端 UI，用户、Agent、系统和工具输出使用不同块样式展示，含状态栏、工具过程展示和窗口内权限确认。
- **Buddy 伙伴 UI**：窗口模式右侧常驻小伙伴，按思考、工具执行、等待批准、完成、错误等阶段切换动作、心情、能量和语言，不遮挡对话内容。
- **持久化记忆**：将有界长期记忆写入项目内 Markdown 文件，启动时自动加载。
- **主动记忆整理**：`/dream` 会提取核心事实、压缩旧条目、按主题归档并限制增长；自动整理会把主题文件写入 `.code-agent/memory/`。
- **穷鬼模式**：`/poor` 会暂停自动持久化记忆并关闭输入时自动建议，降低 token 与交互开销。
- **项目指令**：自动加载 `CODE_AGENT.md`、`CLAUDE.md`、`AGENTS.md` 等项目规则。
- **规则化权限**：支持 session/project 级 allow、deny、ask 规则。
- **计划模式**：先规划，批准后执行，避免计划阶段误执行工具。
- **Shell sandbox**：限制 shell 写入范围、网络访问和危险命令。
- **工具 Hooks**：支持 `pre_tool_use`、`post_tool_use`、`tool_error` 生命周期 hook。
- **MCP 桥接**：可选的 stdio MCP 资源/工具桥接。
- **项目技能**：从 `.code-agent/skills/<name>/SKILL.md` 加载可复用技能。
- **自定义命令**：从 `.code-agent/commands/*.md` 加载项目 slash 命令。
- **项目工作流**：从 `.code-agent/workflows` 运行需要确认的可执行脚本。
- **子 Agent**：支持 fork 单个隔离子 agent，或 coordinator 并行运行多个 worker。
- **后台监控**：在当前会话内启动长运行命令并读取有界输出。

## 安装

```bash
git clone <repository-url>
cd code-agent
pip install -e ".[dev]"
```

## 配置

可以在项目根目录创建 `config.yaml`，也可以通过 `--config path/to/config.yaml` 指定配置文件。CLI 不会读取 `~/.config/code-agent/config.yaml`。

```yaml
llm:
  base_url: "https://api.openai.com/v1"
  api_key: "${OPENAI_API_KEY}"
  model_name: "gpt-4o"
  temperature: 0.2
  max_tokens: 4096

agent:
  max_iterations: 15
  auto_confirm: false
  verbose: false
  context_token_limit: 120000
  auto_compact_token_ratio: 0.85
  max_tool_result_chars: 12000
  persistent_memory_enabled: true
  persistent_memory_path: ".code_agent_memory.md"
  persistent_memory_max_chars: 12000
  poor_mode: false
  poor_mode_path: ".code-agent/runtime.yaml"
  transcript_enabled: true
  transcript_dir: ".code-agent/transcripts"
  transcript_max_event_chars: 20000
  permission_settings_path: ".code-agent/settings.yaml"
  hook_settings_path: ".code-agent/hooks.yaml"
  plan_store_path: ".code-agent/approved_plan.md"
  buddy_settings_path: ".code-agent/buddy.yaml"
  mcp_config_path: ".code-agent/mcp.yaml"
  skills_dir: ".code-agent/skills"
  commands_dir: ".code-agent/commands"
  workflows_dir: ".code-agent/workflows"
  monitor_max_output_chars: 12000
  project_instruction_files:
    - "CODE_AGENT.md"
    - ".code-agent/instructions.md"
    - "CLAUDE.md"
    - "AGENTS.md"
  project_instruction_max_chars: 12000

buddy:
  enabled: false
  base_url: ""
  api_key: ""
  model_name: ""
  temperature: 0.9
  max_tokens: 80
  timeout: 8
  max_retries: 0

shell:
  workspace_root: "."
  timeout: 30
  require_confirmation: true
  sandbox_enabled: true
  sandbox_allow_network: true
  sandbox_writable_paths:
    - "."
  sandbox_deny_write_paths:
    - ".git/config"
    - ".code-agent/settings.yaml"
    - ".code-agent/skills"
    - ".code-agent/commands"
    - "config.yaml"

file:
  workspace_root: "."
  allow_absolute_paths: false

mcp:
  enabled: false
  servers: {}
```

命令行参数也可以覆盖配置：

```bash
code-agent --base-url https://api.example.com/v1 --api-key sk-xxx --model gpt-4o
```

环境变量示例：

```bash
export OPENAI_API_KEY=sk-xxx
export CODE_AGENT_LLM__BASE_URL=https://api.openai.com/v1
export CODE_AGENT_LLM__MODEL_NAME=gpt-4o
code-agent
```

## 使用方式

```bash
# 普通交互模式
code-agent

# 窗口式终端 UI
code-agent --window

# 单次执行
code-agent -p "阅读 README 并总结项目结构"

# 从文件读取 prompt
code-agent -f prompt.txt

# 检查 API 是否可用
code-agent --check-api

# 列出服务商模型 / 运行诊断
code-agent --list-models
code-agent --doctor
```

## 常用命令

- `/help`：显示帮助
- `/exit` 或 `/quit`：退出
- `/clear`：清空对话记忆
- `/config`：显示当前配置
- `/tools`：列出可用工具
- `/tool-search <关键词>`：按能力搜索工具，并激活匹配工具供后续请求使用
- `/model <模型名>`：切换本次会话模型
- `/status`：查看 agent 状态
- `/context`：查看上下文预算和压缩压力
- `/memory`：查看持久化记忆摘要
- `/transcript`：查看会话记录文件
- `/export-transcript [路径]`：导出会话记录为 Markdown
- `/check-api`：检查模型 API 是否可用
- `/models`：列出服务商模型
- `/doctor`：运行本地与服务商诊断
- `/compact`：压缩当前对话上下文
- `/dream`：主动整理持久化记忆，生成主题归档并限制文件增长
- `/poor`：开启或关闭穷鬼模式，暂停自动记忆保存并关闭输入时自动建议
- `/plan`：开启或关闭计划模式
- `/ultraplan <任务>`：生成增强计划，包含分阶段步骤、风险、权限点和验证标准
- `/approve-plan`：批准最近一次计划
- `/execute-plan`：执行已批准计划
- `/clear-plan`：清除已批准计划
- `/permissions`：查看权限规则
- `/allow <工具> [匹配内容]`：本会话允许某工具
- `/deny <工具> [匹配内容]`：本会话拒绝某工具
- `/allow-project <工具> [匹配内容]`：写入项目级允许规则
- `/deny-project <工具> [匹配内容]`：写入项目级拒绝规则
- `/hooks`：查看工具 hooks
- `/buddy [hatch|card|pet|cheer|joke|roast|snack|chat|mute|unmute|off|reset]`：开启、查看或管理窗口右侧 Buddy 伙伴
- `/skills`：列出项目技能
- `/discover-skills <任务>`：按当前任务发现相关技能，不加载完整技能正文
- `/skill <技能名> [参数]`：运行项目技能
- `/commands`：列出项目自定义命令
- `/workflows`：列出项目 workflow 脚本
- `/workflow <脚本名> [参数]`：运行项目 workflow
- `/monitor <命令>`：启动会话内后台监控
- `/monitors`：列出监控任务
- `/monitor-read <任务ID>`：读取监控输出
- `/monitor-stop <任务ID>`：停止监控任务
- `/fork <任务>`：运行一个隔离子 agent
- `/coordinator <标题: 任务; 标题: 任务>`：并行运行多个 worker 子 agent
- `/cost`：查看 token/费用统计

## 补全与历史

- 输入 `/` 后按 `Tab` 会联想命令。
- `/skill <前缀>` 会补全项目技能名。
- `/workflow <前缀>` 会补全 workflow 脚本名。
- `/allow`、`/deny` 等命令会补全工具名。
- 输入路径片段如 `src/co`、`./`、`~/` 后按 `Tab` 会补全文件路径。
- `Ctrl-R` 可搜索历史输入，`↑/↓` 可浏览历史。

## 记忆整理

Code Agent 会把长期记忆写入项目内 `.code_agent_memory.md`，并把主题化记忆写入 `.code-agent/memory/*.md`。普通保存会保留最近重要上下文；当你希望主动清理和归档时，可以运行：

```text
/dream
```

`/dream` 会读取已有 Markdown 记忆和当前会话记忆，执行以下整理：

- 提取核心事实和用户偏好
- 合并重复条目
- 将内容归入“核心事实、用户偏好、项目约束、当前任务、文件与工具、错误与修正、工作日志”等主题
- 重写记忆文件，保证不超过配置的 `persistent_memory_max_chars`
- 将主题归档同步到 `.code-agent/memory/`，启动时会一起加载
- 当持久化文件接近上限且重要消息足够多时，会自动执行一次整理，避免无限增长

## Buddy 伙伴 UI

窗口模式下运行 `/buddy` 会在右侧开启常驻 Buddy 面板。面板占用独立侧栏，不覆盖对话记录；Agent 回复很长时会在左侧对话区自动换行。Buddy 状态写入项目内 `.code-agent/buddy.yaml`，再次启动窗口模式会保持开启，除非运行：

```text
/buddy off
```

常用操作：

- `/buddy pet`：互动一次，更新动作和语言
- `/buddy cheer`：让 Buddy 给你打气，同时提升能量和默契
- `/buddy joke`：让 Buddy 讲一句轻松的代码冷笑话
- `/buddy roast`：让 Buddy 轻轻吐槽当前问题，不攻击用户
- `/buddy snack`：投喂 Buddy，恢复能量并更新陪伴语气
- `/buddy chat <内容>`：和 Buddy 说一句话，只更新右侧伙伴，不调用主 Agent
- `/buddy mute` / `/buddy unmute`：只静音或恢复语言，动作仍随任务阶段变化
- `/buddy card`：查看伙伴卡片
- `/buddy reset`：重新孵化项目伙伴

Buddy 默认使用本地规则即时切换动作和语言，不消耗模型请求。如果希望它更像一个会接话的伙伴，可以开启独立模型通道：

```yaml
buddy:
  enabled: true
  model_name: "gpt-4o-mini"
  proactive_enabled: true
  proactive_interval_seconds: 90
  proactive_min_idle_seconds: 45
```

`base_url`、`api_key`、`model_name` 留空时会复用主 LLM 配置；这个通道只生成右侧面板的一句话，不读取完整对话记忆、不调用工具，也不会影响主 Agent 的代码执行流程。
Buddy 开启后会在窗口空闲时按配置主动刷新右侧面板文案；这些主动陪伴不会写入主 Agent 对话记忆，也不会打断当前工具执行。

## 工具搜索和语义导航

工具注册表会保存所有工具元数据，但每轮只把基础工具和当前请求相关工具暴露给模型。`tool_search` 可按关键词搜索并 pin 住匹配工具，适合“我不确定该用哪个工具”的场景。`lsp_tool` 提供轻量语义能力：

```text
action=symbols       列出类、函数、结构等符号
action=definition    按符号名查定义
action=references    按词查引用位置
action=diagnostics   做基础 Python 语法诊断
```

## 穷鬼模式

`/poor` 借鉴 Claude Code Best 的 Poor Mode 思路：当你想降低消耗或减少窗口交互卡顿时，它会暂停自动持久化记忆保存，关闭输入时自动建议，只保留手动 `Tab` 补全。状态会写入项目内 `.code-agent/runtime.yaml`，不包含 API Key，也不会修改 `config.yaml`。再次运行 `/poor` 会恢复正常模式。

## 项目技能

在 `.code-agent/skills/<name>/SKILL.md` 中创建技能：

```markdown
---
description: 严格审查一次代码变更
argument_hint: 文件或功能名
keywords: [review, 审查, 测试]
allowed_tools: [read_file, grep, git_diff]
---

审查 $ARGUMENTS，重点关注正确性、遗漏测试和潜在回归。
```

运行：

```text
/skill review src/code_agent/cli.py
```

## 自定义命令

`.code-agent/commands/fix.md` 会成为 `/fix`：

```markdown
---
description: 修复一个失败测试
argument_hint: 测试名或失败信息
---

检查 $ARGUMENTS 对应失败，做最小安全修复，并重新运行相关测试。
```

## Workflow 脚本

`.code-agent/workflows` 中的可执行文件会被识别为 workflow：

```bash
mkdir -p .code-agent/workflows
cat > .code-agent/workflows/test <<'SH'
#!/bin/sh
pytest -q "$@"
SH
chmod +x .code-agent/workflows/test
```

运行：

```text
/workflow test
```

## 子 Agent 与后台监控

`/fork <任务>` 会派生一个继承当前记忆摘要的隔离子 agent。`/coordinator <标题: 任务; 标题: 任务>` 可并行运行多个独立 worker。它们是当前进程内的子 agent，不是跨重启持久化的 daemon session。

`/monitor <命令>` 用于启动长运行命令，例如 dev server 或 test watcher。监控任务仅在当前 CLI 进程中存在，并保留有界最近输出。

## Hooks

创建 `.code-agent/hooks.yaml` 可以把命令挂到工具生命周期：

```yaml
hooks:
  pre_tool_use:
    - command: "python scripts/check_tool.py"
      timeout: 10
      continue_on_error: false
  post_tool_use:
    - command: "python scripts/log_tool.py"
```

Hook 命令会收到 `CODE_AGENT_TOOL_NAME`、`CODE_AGENT_HOOK_EVENT` 和 `CODE_AGENT_TOOL_PARAM_*` 环境变量。

## 项目结构

```text
code-agent/
├── src/code_agent/
│   ├── cli.py              # CLI 入口
│   ├── agent/              # Agent 核心、记忆、prompt
│   ├── config/             # 配置模型
│   ├── tools/              # 文件、编辑、shell、git、web、LSP、tool_search、MCP、技能等工具
│   ├── ui/                 # 普通 CLI、窗口 UI、补全、权限提示
│   └── utils/              # 路径、安全、记忆、hooks、子 agent 等工具函数
├── tests/                  # 测试套件
├── CODE_AGENT.md           # 项目本地指令
├── config.yaml             # 本地配置
└── README.md
```

## 质量检查

```bash
ruff check src tests
mypy src/code_agent
pytest -q
```
