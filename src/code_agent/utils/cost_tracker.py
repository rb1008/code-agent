"""成本追踪模块 - 追踪 Token 使用量和估算费用

参考主流编码代理的成本追踪工作流实现。
支持多种模型的 token 计数和费用估算。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar


@dataclass
class UsageRecord:
    """单次使用记录"""
    timestamp: str
    model: str
    input_tokens: int
    output_tokens: int
    cost: float  # 美元
    operation: str  # 操作类型：chat, compact, summary 等


@dataclass
class ModelPricing:
    """模型定价信息"""
    name: str
    input_price_per_1k: float   # 每 1K 输入 token 的价格（美元）
    output_price_per_1k: float  # 每 1K 输出 token 的价格（美元）


# 默认模型定价（美元）
DEFAULT_PRICING = {
    "gpt-4o": ModelPricing("gpt-4o", 0.0025, 0.01),
    "gpt-4o-mini": ModelPricing("gpt-4o-mini", 0.00015, 0.0006),
    "gpt-4": ModelPricing("gpt-4", 0.03, 0.06),
    "gpt-4-turbo": ModelPricing("gpt-4-turbo", 0.01, 0.03),
    "gpt-3.5-turbo": ModelPricing("gpt-3.5-turbo", 0.0005, 0.0015),
    "claude-3-5-sonnet": ModelPricing("claude-3-5-sonnet", 0.003, 0.015),
    "claude-3-opus": ModelPricing("claude-3-opus", 0.015, 0.075),
    "claude-3-haiku": ModelPricing("claude-3-haiku", 0.00025, 0.00125),
}


class CostTracker:
    """成本追踪器
    
    追踪 LLM API 的 token 使用量和费用。
    支持按模型、按操作类型统计。
    """
    
    _records: ClassVar[list[UsageRecord]] = []
    _pricing: ClassVar[dict[str, ModelPricing]] = DEFAULT_PRICING.copy()

    def __init__(self) -> None:
        """初始化成本追踪器。记录在进程内共享，方便 /cost 查看当前会话。"""
    
    def record_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        operation: str = "chat",
    ) -> float:
        """记录一次使用量
        
        Args:
            model: 模型名称
            input_tokens: 输入 token 数
            output_tokens: 输出 token 数
            operation: 操作类型
            
        Returns:
            本次费用（美元）
        """
        cost = self._calculate_cost(model, input_tokens, output_tokens)
        
        record = UsageRecord(
            timestamp=datetime.now().isoformat(),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            operation=operation,
        )
        
        self._records.append(record)
        return cost
    
    def _calculate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """计算费用
        
        Args:
            model: 模型名称
            input_tokens: 输入 token 数
            output_tokens: 输出 token 数
            
        Returns:
            费用（美元）
        """
        pricing = self._pricing.get(model)
        
        if not pricing:
            # 未知模型，使用默认定价
            pricing = ModelPricing(model, 0.01, 0.03)
        
        input_cost = (input_tokens / 1000) * pricing.input_price_per_1k
        output_cost = (output_tokens / 1000) * pricing.output_price_per_1k
        
        return round(input_cost + output_cost, 6)
    
    def get_total_cost(self) -> float:
        """获取总费用
        
        Returns:
            总费用（美元）
        """
        return round(sum(r.cost for r in self._records), 6)
    
    def get_total_tokens(self) -> dict[str, int]:
        """获取总 token 数
        
        Returns:
            {"input": 输入总数, "output": 输出总数}
        """
        total_input = sum(r.input_tokens for r in self._records)
        total_output = sum(r.output_tokens for r in self._records)
        return {
            "input": total_input,
            "output": total_output,
            "total": total_input + total_output,
        }
    
    def get_stats_by_model(self) -> dict[str, dict[str, Any]]:
        """按模型统计
        
        Returns:
            {模型名: 统计信息}
        """
        stats: dict[str, dict] = {}
        
        for record in self._records:
            if record.model not in stats:
                stats[record.model] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost": 0.0,
                }
            
            stats[record.model]["calls"] += 1
            stats[record.model]["input_tokens"] += record.input_tokens
            stats[record.model]["output_tokens"] += record.output_tokens
            stats[record.model]["cost"] += record.cost
        
        # 四舍五入费用
        for model_stats in stats.values():
            model_stats["cost"] = round(model_stats["cost"], 6)
        
        return stats
    
    def get_stats_by_operation(self) -> dict[str, dict[str, Any]]:
        """按操作类型统计
        
        Returns:
            {操作类型: 统计信息}
        """
        stats: dict[str, dict] = {}
        
        for record in self._records:
            if record.operation not in stats:
                stats[record.operation] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost": 0.0,
                }
            
            stats[record.operation]["calls"] += 1
            stats[record.operation]["input_tokens"] += record.input_tokens
            stats[record.operation]["output_tokens"] += record.output_tokens
            stats[record.operation]["cost"] += record.cost
        
        for op_stats in stats.values():
            op_stats["cost"] = round(op_stats["cost"], 6)
        
        return stats
    
    def get_report(self) -> str:
        """生成成本报告
        
        Returns:
            格式化的成本报告
        """
        if not self._records:
            return "暂无用量记录。"
        
        total_cost = self.get_total_cost()
        total_tokens = self.get_total_tokens()
        model_stats = self.get_stats_by_model()
        
        lines = [
            "📊 成本报告",
            "=" * 50,
            "",
            f"总成本: ${total_cost:.6f}",
            f"总 Token: {total_tokens['total']:,}",
            f"  - 输入: {total_tokens['input']:,}",
            f"  - 输出: {total_tokens['output']:,}",
            f"调用次数: {len(self._records)}",
            "",
            "按模型统计:",
            "-" * 30,
        ]
        
        for model, stats in model_stats.items():
            lines.append(
                f"  {model}: {stats['calls']} 次调用, "
                f"${stats['cost']:.6f}, "
                f"{stats['input_tokens'] + stats['output_tokens']:,} token"
            )
        
        lines.append("")
        lines.append("最近用量:")
        lines.append("-" * 30)
        
        # 显示最近 5 条记录
        for record in self._records[-5:]:
            time = record.timestamp.split("T")[1].split(".")[0]
            lines.append(
                f"  [{time}] {record.model}: "
                f"{record.input_tokens + record.output_tokens} token, "
                f"${record.cost:.6f}"
            )
        
        return "\n".join(lines)
    
    def clear(self) -> None:
        """清除所有记录"""
        self._records.clear()
    
    def add_custom_pricing(
        self,
        model: str,
        input_price: float,
        output_price: float,
    ) -> None:
        """添加自定义模型定价
        
        Args:
            model: 模型名称
            input_price: 每 1K 输入 token 价格
            output_price: 每 1K 输出 token 价格
        """
        self._pricing[model] = ModelPricing(model, input_price, output_price)
