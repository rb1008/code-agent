# 开发指南

## 快速开始

### 1. 克隆项目
```bash
git clone <repository-url>
cd code-agent
```

### 2. 安装依赖
```bash
# 使用 pip
pip install -e ".[dev]"

# 或使用 uv（推荐，更快）
uv pip install -e ".[dev]"
```

### 3. 配置
```bash
# 复制配置模板
cp config.example.yaml config.yaml

# 设置 API Key（推荐使用环境变量）
export OPENAI_API_KEY=your-api-key-here
```

### 4. 运行
```bash
code-agent
```

## 开发工作流

### 运行测试
```bash
# 运行所有测试
pytest

# 运行测试并生成覆盖率报告
pytest --cov=src/code_agent --cov-report=html

# 查看覆盖率报告
open htmlcov/index.html
```

### 代码质量检查
```bash
# 格式化代码
black src tests

# Lint 检查
ruff check src tests

# 类型检查
mypy src
```

### 调试
```bash
# 启用详细输出
code-agent --verbose

# 查看日志
tail -f ~/.config/code-agent/code-agent.log
```

## 项目结构

```
code-agent/
├── src/code_agent/
│   ├── agent/          # Agent 核心逻辑
│   │   ├── core.py     # ReAct Agent 实现
│   │   ├── memory.py   # 会话记忆管理
│   │   └── prompts.py  # 系统提示词
│   ├── tools/          # 工具实现
│   │   ├── file.py     # 文件操作
│   │   ├── shell.py    # Shell 执行
│   │   ├── git.py      # Git 操作
│   │   └── ...
│   ├── ui/             # 用户界面
│   ├── config/         # 配置管理
│   └── utils/          # 工具函数
├── tests/              # 测试套件
├── config.yaml         # 配置文件（不提交）
└── config.example.yaml # 配置模板
```

## 贡献指南

### 添加新工具

1. 在 `src/code_agent/tools/` 创建新文件
2. 继承 `BaseTool` 类
3. 实现 `execute()` 方法
4. 在 `registry.py` 中注册工具
5. 添加测试

示例：
```python
from code_agent.tools.base import BaseTool, ToolResult, ToolPermission

class MyTool(BaseTool):
    name = "my_tool"
    description = "工具说明"
    parameters = {
        "param1": {
            "type": "string",
            "description": "参数说明",
            "required": True,
        }
    }
    permission = ToolPermission(require_confirmation=False)
    
    async def execute(self, param1: str) -> ToolResult:
        # 实现逻辑
        return ToolResult.ok("成功")
```

### 提交代码

1. 确保所有测试通过
2. 运行代码质量检查
3. 更新文档
4. 提交 PR

## 常见问题

### Q: 如何切换模型？
A: 在交互模式下使用 `/model <model-name>` 命令，或修改 `config.yaml`

### Q: 如何启用自动确认？
A: 设置 `agent.auto_confirm: true` 或使用 `--auto-confirm` 参数

### Q: 如何查看成本统计？
A: 在交互模式下使用 `/cost` 命令

## 许可证

MIT License
