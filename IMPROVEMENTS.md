# Code Agent 项目改进清单

## ✅ 已完成的改进

### 1. 安全性改进
- [x] 创建 `.gitignore` 文件，防止敏感文件提交
- [x] 确认 `config.yaml` 由项目内配置管理；用户当前明确要求保留硬编码 API key
- [x] 创建 `config.example.yaml` 配置模板

### 2. 开发工具改进
- [x] 添加 `pytest-cov` 依赖用于测试覆盖率
- [x] 创建 `requirements.txt` 锁定依赖版本
- [x] 创建 `DEVELOPMENT.md` 开发指南

### 3. 日志系统
- [x] 创建 `utils/logger.py` 日志工具模块

## 🔄 建议实施的改进

### 高优先级

#### 1. 集成日志系统
**文件**: `src/code_agent/cli.py`, `src/code_agent/agent/core.py`

**操作**:
```python
# 在 cli.py 中初始化日志
from code_agent.utils.logger import setup_logger

logger = setup_logger(
    name="code_agent",
    level="DEBUG" if verbose else "INFO",
    log_file=Path.home() / ".config" / "code-agent" / "code-agent.log",
    verbose=verbose,
)
```

**替换所有 print() 为 logger 调用**:
- `print()` → `logger.info()`
- 错误信息 → `logger.error()`
- 调试信息 → `logger.debug()`

#### 2. 改进内存压缩策略
**文件**: `src/code_agent/agent/memory.py`

**操作**:
- 实现基于 LLM 的智能摘要生成
- 保留关键上下文（文件路径、重要决策）
- 添加摘要质量评估

示例实现:
```python
async def _compact_with_llm(self) -> str:
    """使用 LLM 生成高质量摘要"""
    messages_text = "\n".join([
        f"{msg.role}: {msg.content[:200]}"
        for msg in self.messages
    ])
    
    prompt = f"请总结以下对话，保留关键事实、文件路径、决策和错误修正：\n{messages_text}"
    # 调用 LLM 生成摘要
    summary = await self.llm.ainvoke(prompt)
    return summary
```

#### 3. 细化异常处理
**文件**: `src/code_agent/agent/core.py`, 各工具文件

**操作**:
- 定义自定义异常类
- 区分不同类型的错误
- 记录完整堆栈跟踪

示例:
```python
# 定义异常类
class AgentError(Exception):
    """Agent 基础异常"""
    pass

class ToolExecutionError(AgentError):
    """工具执行失败"""
    pass

class LLMError(AgentError):
    """LLM API 错误"""
    pass

# 使用
try:
    result = await tool.execute(**kwargs)
except ToolExecutionError as e:
    logger.error(f"工具执行失败: {e}", exc_info=True)
    raise
```

### 中优先级

#### 4. 添加测试覆盖率目标
**操作**:
```bash
# 运行测试并检查覆盖率
pytest --cov=src/code_agent --cov-report=html --cov-fail-under=80

# 添加到 CI/CD 流程
```

#### 5. 优化异步事件循环
**文件**: `src/code_agent/cli.py`

**选项 A**: 完全异步化
```python
async def main_async():
    # 所有逻辑改为异步
    pass

if __name__ == "__main__":
    asyncio.run(main_async())
```

**选项 B**: 完全同步化
- 移除 async/await
- 使用同步 API

#### 6. 添加性能监控
**操作**:
- 记录每个工具的执行时间
- 记录 LLM 调用延迟
- 生成性能报告

### 低优先级

#### 7. 增强文档
- [ ] 添加 API 文档（使用 Sphinx）
- [ ] 添加架构图
- [ ] 添加更多使用示例

#### 8. 添加 CI/CD
- [ ] GitHub Actions 配置
- [ ] 自动化测试
- [ ] 自动化发布

## 📊 质量指标目标

- **测试覆盖率**: > 80%
- **类型注解覆盖**: 100%（已达成 ✅）
- **代码质量**: Ruff 0 错误
- **文档完整性**: 所有公共 API 有文档

## 🔧 快速实施命令

```bash
# 1. 安装更新的依赖
pip install -e ".[dev]"

# 2. 运行测试并生成覆盖率
pytest --cov=src/code_agent --cov-report=html

# 3. 检查代码质量
black src tests
ruff check src tests
mypy src

# 4. 查看覆盖率报告
open htmlcov/index.html
```

## 📝 下一步行动

1. **立即**: 集成日志系统（替换所有 print）
2. **本周**: 改进内存压缩策略
3. **本月**: 提高测试覆盖率到 80%
4. **长期**: 添加 CI/CD 和完善文档
