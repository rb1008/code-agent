"""中文命令说明与参数提示。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    """一个 slash 命令的中文说明。"""

    name: str
    summary: str
    usage: str
    arguments: str = ""
    path_args: bool = False


COMMAND_SPECS: list[CommandSpec] = [
    CommandSpec("/help", "显示帮助", "/help"),
    CommandSpec("/exit", "退出程序", "/exit"),
    CommandSpec("/quit", "退出程序", "/quit"),
    CommandSpec("/clear", "清空当前对话记忆", "/clear"),
    CommandSpec("/config", "显示当前配置", "/config"),
    CommandSpec("/tools", "列出可用工具", "/tools"),
    CommandSpec("/tool-search", "按能力搜索工具", "/tool-search <关键词>", "关键词"),
    CommandSpec("/model", "切换本次会话使用的模型", "/model <模型名>", "模型名"),
    CommandSpec("/status", "查看 agent 状态", "/status"),
    CommandSpec("/context", "查看上下文预算与压缩压力", "/context"),
    CommandSpec("/compact", "压缩当前对话上下文", "/compact"),
    CommandSpec("/dream", "主动整理持久化记忆，按主题归档并限制增长", "/dream"),
    CommandSpec("/poor", "开启或关闭省 token 模式，暂停自动记忆并关闭输入建议", "/poor"),
    CommandSpec("/plan", "开启或关闭只读计划模式", "/plan"),
    CommandSpec("/ultraplan", "生成增强计划：深度拆解、风险、验证和批准步骤", "/ultraplan <任务>", "任务"),
    CommandSpec("/approve-plan", "批准最近一次计划", "/approve-plan [计划文本]", "可选计划文本"),
    CommandSpec("/execute-plan", "执行已批准的计划", "/execute-plan"),
    CommandSpec("/clear-plan", "清除已批准的计划", "/clear-plan"),
    CommandSpec("/resume", "恢复上次保存的会话", "/resume"),
    CommandSpec("/save", "保存当前会话", "/save [名称]", "可选名称"),
    CommandSpec("/cost", "查看 token/费用统计", "/cost"),
    CommandSpec("/memory", "查看持久化记忆摘要", "/memory"),
    CommandSpec("/transcript", "查看当前会话记录文件", "/transcript"),
    CommandSpec(
        "/export-transcript",
        "导出会话记录为 Markdown",
        "/export-transcript [输出路径]",
        "输出路径",
        path_args=True,
    ),
    CommandSpec("/check-api", "检查模型 API 是否可用", "/check-api"),
    CommandSpec("/models", "列出服务商返回的模型", "/models"),
    CommandSpec("/doctor", "运行本地与模型服务诊断", "/doctor"),
    CommandSpec("/permissions", "查看权限规则", "/permissions"),
    CommandSpec("/allow", "本会话允许某个工具", "/allow <工具名> [匹配内容]", "工具名/匹配内容"),
    CommandSpec("/deny", "本会话拒绝某个工具", "/deny <工具名> [匹配内容]", "工具名/匹配内容"),
    CommandSpec(
        "/allow-project",
        "写入项目级允许规则",
        "/allow-project <工具名> [匹配内容]",
        "工具名/匹配内容",
    ),
    CommandSpec(
        "/deny-project",
        "写入项目级拒绝规则",
        "/deny-project <工具名> [匹配内容]",
        "工具名/匹配内容",
    ),
    CommandSpec("/hooks", "查看工具生命周期 hooks", "/hooks"),
    CommandSpec(
        "/buddy",
        "开启、查看或管理右下角伙伴 UI",
        "/buddy [hatch|card|pet|cheer|joke|roast|snack|chat|mute|unmute|off|reset]",
        "子命令",
    ),
    CommandSpec("/skills", "列出项目技能", "/skills"),
    CommandSpec("/discover-skills", "按当前任务语义发现相关技能", "/discover-skills <任务>", "任务"),
    CommandSpec("/skill", "运行项目技能", "/skill <技能名> [参数]", "技能名/参数"),
    CommandSpec("/commands", "列出项目自定义命令", "/commands"),
    CommandSpec("/workflows", "列出项目 workflow 脚本", "/workflows"),
    CommandSpec("/workflow", "运行项目 workflow 脚本", "/workflow <脚本名> [参数]", "脚本名/参数"),
    CommandSpec("/monitor", "启动当前会话内的后台监控命令", "/monitor <命令>", "命令"),
    CommandSpec("/monitors", "列出后台监控任务", "/monitors"),
    CommandSpec("/monitor-read", "读取监控任务输出", "/monitor-read <任务ID>", "任务ID"),
    CommandSpec("/monitor-stop", "停止监控任务", "/monitor-stop <任务ID>", "任务ID"),
    CommandSpec("/fork", "派生一个隔离子 agent", "/fork <任务>", "任务"),
    CommandSpec(
        "/coordinator",
        "并行运行多个 worker 子 agent",
        "/coordinator <标题: 任务; 标题: 任务>",
        "标题: 任务; 标题: 任务",
    ),
]

COMMAND_MAP = {command.name: command for command in COMMAND_SPECS}
SLASH_COMMANDS = [command.name for command in COMMAND_SPECS]


def command_hint(text: str) -> str:
    """返回当前输入的中文参数提示。"""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return "Tab 补全命令和路径 │ Ctrl-R 搜索历史 │ ↑/↓ 浏览历史"
    command = stripped.split(maxsplit=1)[0].lower()
    spec = COMMAND_MAP.get(command)
    if not spec:
        return "输入 / 后按 Tab 查看可用命令"
    suffix = f" │ 参数：{spec.arguments}" if spec.arguments else ""
    return f"{spec.usage} │ {spec.summary}{suffix}"
