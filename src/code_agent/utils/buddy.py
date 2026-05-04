"""Project-local Buddy companion state and rendering.

Buddy 是窗口模式里的轻量状态伙伴：它不参与 Agent 决策，只把当前阶段、
等待批准、工具执行和完成状态用稳定的小面板展示出来。
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import getpass
import random
import textwrap
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
import yaml  # type: ignore[import-untyped]


SPECIES = [
    "Duck",
    "Cat",
    "Dragon",
    "Owl",
    "Penguin",
    "Turtle",
    "Ghost",
    "Robot",
    "Rabbit",
    "Mushroom",
]

RARITIES = [
    ("Common", 60),
    ("Uncommon", 25),
    ("Rare", 10),
    ("Epic", 4),
    ("Legendary", 1),
]

MOOD_LABELS = {
    "idle": "歇会儿",
    "queued": "接住了",
    "thinking": "转脑子",
    "plan": "铺路线",
    "ultraplan": "深挖中",
    "tool_prepare": "开工具箱",
    "tool_running": "盯输出",
    "approval": "等你点头",
    "success": "收工",
    "error": "摔了一跤",
    "pet": "被摸摸",
    "cheer": "给你打气",
    "joke": "抖包袱",
    "roast": "轻吐槽",
    "snack": "补能量",
    "chat": "探头聊天",
}

ACTION_LABELS = {
    "idle": "盘腿复盘",
    "queued": "探头接活",
    "thinking": "抱着问号转圈",
    "plan": "摊开小地图",
    "ultraplan": "戴上审查眼镜",
    "tool_prepare": "翻工具箱",
    "tool_running": "盯终端跑步",
    "approval": "举着确认牌",
    "success": "原地庆祝",
    "error": "贴着创可贴",
    "pet": "冒出小爱心",
    "cheer": "挥着小旗",
    "joke": "认真抖包袱",
    "roast": "斜眼吐槽",
    "snack": "抱着小零食",
    "chat": "趴在边上接话",
}

STATE_DELTAS = {
    "queued": {"energy": -1, "focus": 2},
    "thinking": {"energy": -1, "focus": 4},
    "plan": {"focus": 3},
    "ultraplan": {"energy": -2, "focus": 5},
    "tool_prepare": {"focus": 2},
    "tool_running": {"energy": -1, "focus": 2},
    "approval": {"focus": 1},
    "success": {"energy": 5, "focus": -2, "bond": 2, "wins": 1, "streak": 1},
    "error": {"energy": -3, "focus": 2, "bond": 1, "stumbles": 1, "streak_reset": 1},
    "pet": {"energy": 8, "focus": -1, "bond": 4},
    "cheer": {"energy": 7, "focus": 2, "bond": 3},
    "joke": {"energy": 3, "focus": -2, "bond": 2},
    "roast": {"energy": 2, "focus": 1, "bond": 1},
    "snack": {"energy": 12, "focus": -1, "bond": 2},
    "chat": {"energy": -1, "focus": -1, "bond": 2},
}

STATE_LINES: dict[str, list[str]] = {
    "idle": [
        "我还在复盘「{topic}」，这题不坏，就是有点爱绕路。",
        "刚才这轮收住了。下一题来吧，我已经把小板凳搬好了。",
        "现在很安静，适合开工。你一开口，我就上线营业。",
    ],
    "queued": [
        "收到「{topic}」。我先把安全带系上，这题看起来不止三行。",
        "这题我接住了。先别急着冲，冲太快容易把 bug 也带上车。",
        "好，来活了。希望这次问题讲道理，不讲玄学。",
    ],
    "thinking": [
        "我对「{topic}」的第一印象：先看证据，别靠感觉写代码。",
        "脑内索引正在转。「{topic}」如果有坑，我会把坑边插旗。",
        "先看清楚「{topic}」，再动手。优秀开发者和键盘侠就差这几秒。",
    ],
    "plan": [
        "计划板已经展开。路线清楚一点，返工就少一点。",
        "先把「{topic}」拆开，不然它会假装自己很复杂。",
    ],
    "ultraplan": [
        "深度计划启动。这个题我要按审稿人标准挑刺。",
        "我会盯紧风险和验证。放心，我今天不当气氛组。",
    ],
    "tool_prepare": [
        "工具箱打开了。现在让事实说话，少让猜测上桌。",
        "准备调用工具。希望输出诚实一点，不要谜语人。",
    ],
    "tool_running": [
        "工具正在跑。别急，真正的线索通常喜欢迟到。",
        "我在盯输出变化。它敢红，我就敢记下来。",
        "这个步骤处理中。代码现在正在接受小型体检。",
    ],
    "approval": [
        "这一步要你点头。我可以冲，但方向盘在你手里。",
        "我停在批准门口了。门铃已按，等主人发话。",
        "需要确认后才能继续。别担心，我没有偷偷摸摸动文件。",
    ],
    "success": [
        "「{topic}」这轮收住了。今天的代码情绪还算稳定。",
        "结果已经放到对话里。不错，这题被我们按住了。",
        "我看到绿色信号了。可以短暂开心三秒，然后继续严格。",
    ],
    "error": [
        "这里摔了一跤，但线索也掉出来了。我们顺着捡。",
        "有错误，不丢人；现在知道它在哪儿，就已经赢一半。",
        "异常已经冒头了。它最好把话说清楚，不然我继续追。",
    ],
    "pet": [
        "摸到了。生产力 +3，毒舌值暂时 -1。",
        "好，精神了。现在我愿意少吐槽两句。",
        "收到互动。你负责提需求，我负责在右下角认真营业。",
    ],
    "cheer": [
        "给你打气：这题已经被你按到桌边了，再推一下就行。",
        "我站右下角挥旗。你负责推进，我负责把士气续上。",
        "这轮别怕慢，慢是为了少返工。稳一点，也很帅。",
    ],
    "joke": [
        "冷笑话时间：bug 最爱躲哪？躲在“我就改一行”后面。",
        "我刚想讲个笑话，结果发现依赖版本已经替我讲完了。",
        "代码评审像照镜子：不一定好看，但很有必要。",
    ],
    "roast": [
        "轻吐槽：这需求挺有性格，像凌晨三点写的灵感备忘录。",
        "我不说它乱，我只说它很自由，甚至自由得有点跑偏。",
        "这题不是难，是比较会伪装。放心，我会拆它台。",
    ],
    "snack": [
        "补给收到。现在能量条像刚喝了半杯热咖啡。",
        "谢谢投喂。我会把这份能量转化成更少的废话和更多的检查。",
        "小零食到账。右下角续航恢复，继续陪你打这局。",
    ],
    "chat": [
        "我在右下角听着呢。「{topic}」这事可以慢慢拆，不用硬扛。",
        "主动冒泡一下：如果你卡住了，先把最小失败现场抓出来。",
        "我刚巡逻回来，结论是：别急，越急越容易把锅扣给无辜代码。",
        "这会儿适合喝口水再看「{topic}」。大脑缓存也要刷新。",
    ],
}

SNARK_LINES: dict[str, list[str]] = {
    "thinking": [
        "「{topic}」闻起来有点像祖传小坑，我先戴手套看。",
        "「{topic}」要是想骗我直接动手，那它算盘打早了。",
    ],
    "tool_running": [
        "工具在跑。现在是代码自证清白时间，希望它别紧张。",
        "我盯着呢。输出要是开始胡言乱语，我会第一时间翻白眼。",
    ],
    "success": [
        "搞定。它没有想象中硬，主要是之前没人认真盯它。",
        "这轮过了。可以夸一下，但别夸太大声，测试会听见。",
    ],
    "error": [
        "报错来了。很好，它终于肯开口了。",
        "失败得很有态度。现在轮到我们更有态度。",
    ],
}

WARM_LINES: dict[str, list[str]] = {
    "idle": [
        "我在这儿。下一题慢慢来，我们一块儿拆。",
        "刚才那轮辛苦了。你提需求，我守右下角。",
    ],
    "approval": [
        "需要你确认一下。我会等，不抢你的控制权。",
        "这步有影响，所以我停住了。你点头我再继续。",
    ],
    "error": [
        "这一步没过，但问题已经露面了。我们还有路走。",
        "别急，失败不是终点，是线索开始说话。",
        "稳住，我在右下角陪你把这坨线头慢慢理开。",
    ],
    "success": [
        "这一轮很漂亮。你负责方向，我负责在旁边亮灯。",
        "做得好。现在代码舒服一点，我也舒服一点。",
    ],
    "pet": [
        "谢谢摸摸。今天也一起把复杂问题变小一点。",
        "我收到了。你不是一个人在看这些报错。",
    ],
    "cheer": [
        "别急，我们一点点推进。能被说清楚的问题，就能被解决。",
        "你已经把方向给出来了，剩下就是把路铺稳。",
    ],
    "joke": [
        "笑一下也算重启缓存。现在继续看代码，会清醒一点。",
    ],
    "snack": [
        "补能量很好，长任务最怕硬扛。我们稳着来。",
    ],
    "chat": [
        "我会在旁边提醒节奏。长任务拆小，心态会轻很多。",
        "你不用一口气想完全部，先把下一步想清楚就够了。",
    ],
}

SPRITES: dict[str, list[str]] = {
    "idle": [
        "     /\\_/\\",
        "    ( o.o )",
        "    /|___|\\",
        "     /   \\",
    ],
    "queued": [
        "  -> /\\_/\\",
        "    ( o.o )",
        "   </|___|",
        "      / >",
    ],
    "thinking": [
        "    ? /\\_/\\",
        "    ( -.- )",
        "    /| ? |\\",
        "      / \\",
    ],
    "plan": [
        "    /\\_/\\",
        "   ( o_O )",
        "  _/|map|\\_",
        "     / \\",
    ],
    "ultraplan": [
        "   * /\\_/\\",
        "    ( *.* )",
        "   _/|#|\\_",
        "    /_|_\\",
    ],
    "tool_prepare": [
        "    /\\_/\\  /",
        "   ( 0.0 )/",
        "   /|box|",
        "    /   \\",
    ],
    "tool_running": [
        "   /\\_/\\  ...",
        "  ( >.> )",
        " ==/|run|>",
        "    /  >",
    ],
    "approval": [
        "    /\\_/\\",
        "   ( ! ! )",
        "   /|Y/n|\\",
        "     / \\",
    ],
    "success": [
        "  \\ /\\_/\\ /",
        "   ( ^.^ )",
        "    /|v|\\",
        "    / \\",
    ],
    "error": [
        "    /\\_/\\",
        "   ( x.x )",
        "   /|fix|\\",
        "   _/ \\_",
    ],
    "pet": [
        "   <3 /\\_/\\",
        "    ( ^_^ )",
        "    /|<3|\\",
        "      / \\",
    ],
    "cheer": [
        "    /\\_/\\  /",
        "   ( ^.^ )/",
        "   /|flag|",
        "     / \\",
    ],
    "joke": [
        "    /\\_/\\  ?",
        "   ( ^o^ )",
        "   /|haha|\\",
        "     / \\",
    ],
    "roast": [
        "    /\\_/\\",
        "   ( -_- )",
        "   /|tsk|\\",
        "    /  \\",
    ],
    "snack": [
        "    /\\_/\\",
        "   ( o.o )",
        "   /|nom|\\",
        "    /   \\",
    ],
    "chat": [
        "    /\\_/\\",
        "   ( o.o )  hi",
        "   /|___|\\",
        "    /   \\",
    ],
}


@dataclass
class BuddyState:
    """Persistent companion identity plus runtime display state."""

    enabled: bool = False
    muted: bool = False
    name: str = "Byte"
    species: str = "Robot"
    rarity: str = "Common"
    shiny: bool = False
    personality: str = "安静但可靠的代码伙伴"
    debugging: int = 50
    patience: int = 50
    chaos: int = 50
    wisdom: int = 50
    snark: int = 50
    mood: str = "idle"
    action: str = ACTION_LABELS["idle"]
    energy: int = 70
    focus: int = 40
    bond: int = 20
    wins: int = 0
    stumbles: int = 0
    streak: int = 0
    message: str = ""
    detail: str = ""

    @classmethod
    def hatch(cls, seed_text: str) -> "BuddyState":
        """Create a deterministic buddy from stable local seed data."""
        seed = int(sha256(seed_text.encode("utf-8")).hexdigest()[:16], 16)
        rng = random.Random(seed)
        species = rng.choice(SPECIES)
        rarity = _weighted_choice(rng, RARITIES)
        shiny = rng.randint(1, 100) == 1
        stats = {
            "debugging": rng.randint(20, 100),
            "patience": rng.randint(20, 100),
            "chaos": rng.randint(0, 100),
            "wisdom": rng.randint(20, 100),
            "snark": rng.randint(0, 100),
        }
        name = _name_for(seed, species)
        return cls(
            enabled=True,
            name=name,
            species=species,
            rarity=rarity,
            shiny=shiny,
            personality=_personality(stats),
            debugging=stats["debugging"],
            patience=stats["patience"],
            chaos=stats["chaos"],
            wisdom=stats["wisdom"],
            snark=stats["snark"],
            energy=rng.randint(55, 85),
            focus=rng.randint(25, 65),
            bond=rng.randint(10, 35),
            message=_line_for("idle", seed),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "muted": self.muted,
            "name": self.name,
            "species": self.species,
            "rarity": self.rarity,
            "shiny": self.shiny,
            "personality": self.personality,
            "debugging": self.debugging,
            "patience": self.patience,
            "chaos": self.chaos,
            "wisdom": self.wisdom,
            "snark": self.snark,
            "mood": self.mood,
            "action": self.action,
            "energy": self.energy,
            "focus": self.focus,
            "bond": self.bond,
            "wins": self.wins,
            "stumbles": self.stumbles,
            "streak": self.streak,
            "message": self.message,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BuddyState":
        defaults = cls()
        values = defaults.to_dict()
        values.update({key: value for key, value in data.items() if key in values})
        return cls(**values)


class BuddyStore:
    """Load and save Buddy state in a project-local YAML file."""

    def __init__(self, path: Path, seed_text: str | None = None) -> None:
        self.path = path
        self.seed_text = seed_text or f"{getpass.getuser()}:{Path.cwd().resolve()}"

    def load(self) -> BuddyState:
        if not self.path.exists():
            return BuddyState()
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return BuddyState()
        return BuddyState.from_dict(data)

    def save(self, state: BuddyState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            yaml.safe_dump(state.to_dict(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def ensure(self) -> BuddyState:
        state = self.load()
        if not state.name or state.name == "Byte":
            state = BuddyState.hatch(self.seed_text)
            self.save(state)
        return state

    def hatch(self, *, reset: bool = False) -> BuddyState:
        if self.path.exists() and not reset:
            state = self.load()
            state.enabled = True
            self.save(state)
            return state
        state = BuddyState.hatch(self.seed_text)
        self.save(state)
        return state


class BuddyRenderer:
    """Render Buddy state for CLI and prompt-toolkit panels."""

    width = 28

    def render_panel(self, state: BuddyState) -> str:
        if not state.enabled:
            return ""
        sprite = _sprite(state)
        lines = [
            f"{state.name} · {state.rarity}{' shiny' if state.shiny else ''}",
            f"{state.species} · {MOOD_LABELS.get(state.mood, state.mood)}",
            f"动作 · {state.action}",
            f"手感 · {_vibe_label(state)}",
            f"徽章 · {_badge_label(state)}",
            f"能量 {_meter(state.energy)}",
            f"默契 {_meter(state.bond)}",
            "",
            *sprite,
        ]
        if state.detail:
            lines.extend(["", *_wrap(f"当前 · {state.detail}", self.width, max_lines=2)])
        if not state.muted and state.message:
            lines.extend(["", *_wrap(f"“{state.message}”", self.width, max_lines=3)])
        lines.append(f"战绩 · {state.wins} 胜/{state.stumbles} 坑 · 连续 {state.streak}")
        lines.append("互动 · pet cheer joke snack chat")
        return "\n".join(lines)

    def render_card(self, state: BuddyState) -> str:
        status = "开启" if state.enabled else "关闭"
        muted = "静音" if state.muted else "会说话"
        shiny = "是" if state.shiny else "否"
        return "\n".join(
            [
                f"Buddy: {state.name}",
                f"状态: {status} / {muted}",
                f"物种: {state.species}",
                f"稀有度: {state.rarity}",
                f"Shiny: {shiny}",
                f"个性: {state.personality}",
                "",
                f"DEBUGGING: {state.debugging}",
                f"PATIENCE: {state.patience}",
                f"CHAOS: {state.chaos}",
                f"WISDOM: {state.wisdom}",
                f"SNARK: {state.snark}",
                "",
                f"ENERGY: {state.energy}",
                f"FOCUS: {state.focus}",
                f"BOND: {state.bond}",
                f"WINS: {state.wins}",
                f"STUMBLES: {state.stumbles}",
                f"STREAK: {state.streak}",
            ]
        )


def update_buddy_state(
    state: BuddyState,
    mood: str,
    *,
    detail: str = "",
    nonce: str = "",
    user_text: str = "",
) -> BuddyState:
    """Mutate Buddy with mood-specific movement and contextual language."""
    state.mood = mood if mood in STATE_LINES else "idle"
    state.action = ACTION_LABELS.get(state.mood, ACTION_LABELS["idle"])
    state.detail = detail.strip()
    _apply_state_delta(state)
    topic = _topic_from_user_text(user_text)
    seed_text = f"{state.name}:{state.mood}:{state.detail}:{topic}:{nonce}"
    seed = int(sha256(seed_text.encode()).hexdigest()[:8], 16)
    state.message = _line_for(state, seed, topic=topic, detail=state.detail)
    return state


class BuddyBrain:
    """Independent optional LLM voice generator for Buddy.

    It never receives the main conversation memory, never calls tools, and only
    returns one short line for the side panel. Local deterministic text remains
    the fallback whenever this channel is disabled or unavailable.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        base_url: str = "",
        api_key: str = "",
        model_name: str = "",
        temperature: float = 0.9,
        max_tokens: int = 80,
        timeout: int = 8,
        max_retries: int = 0,
        llm: Any | None = None,
    ) -> None:
        self.enabled = enabled and bool(llm or (base_url and api_key and model_name))
        self._llm = llm
        if self.enabled and self._llm is None:
            self._llm = ChatOpenAI(
                base_url=base_url,
                api_key=SecretStr(api_key),
                model=model_name,
                temperature=temperature,
                max_completion_tokens=max_tokens,
                timeout=timeout,
                max_retries=max_retries,
                default_headers={
                    "User-Agent": "Code-Agent-Buddy/0.1.0",
                    "Accept": "application/json",
                },
            )

    async def generate(
        self,
        state: BuddyState,
        *,
        user_text: str,
        detail: str,
    ) -> str | None:
        """Generate one short Buddy line, returning None on disabled/empty output."""
        if not self.enabled or self._llm is None or state.muted:
            return None

        topic = _topic_from_user_text(user_text)
        system = (
            "你是 Code Agent 右下角的 Buddy 宠物，只负责情绪价值和轻微吐槽。"
            "只返回一句中文，不超过 34 个汉字；不要解释、不要 Markdown、不要代码、"
            "不要承诺已经执行操作、不要替用户做权限决定。可以开玩笑、毒舌、鼓励，"
            "但不要攻击用户，不要重复状态栏文字。整体基调要温暖、陪伴、有一点个性，"
            "像真的在旁边陪用户写代码。"
        )
        human = "\n".join(
            [
                f"宠物名：{state.name}",
                f"物种：{state.species}",
                f"性格：{state.personality}",
                f"阶段：{MOOD_LABELS.get(state.mood, state.mood)}",
                f"动作：{state.action}",
                f"心情：{_vibe_label(state)}",
                f"能量：{state.energy}",
                f"默契：{state.bond}",
                f"连胜：{state.streak}",
                f"用户主题：{topic}",
                f"当前细节：{_clip_phrase(detail, 40) or '无'}",
                f"毒舌值：{state.snark}",
                f"耐心值：{state.patience}",
                "请输出一句像宠物在旁边说的话。",
            ]
        )
        response = await self._llm.ainvoke([SystemMessage(content=system), HumanMessage(content=human)])
        content = getattr(response, "content", "")
        if not isinstance(content, str):
            content = str(content)
        return _sanitize_model_line(content)


def _weighted_choice(rng: random.Random, choices: list[tuple[str, int]]) -> str:
    total = sum(weight for _name, weight in choices)
    roll = rng.randint(1, total)
    upto = 0
    for name, weight in choices:
        upto += weight
        if roll <= upto:
            return name
    return choices[0][0]


def _name_for(seed: int, species: str) -> str:
    syllables = ["Mo", "Ki", "Lu", "Bo", "Nia", "Tao", "Zed", "Mika", "Rin", "Pip"]
    rng = random.Random(seed + 17)
    return f"{rng.choice(syllables)}-{species[:3]}"


def _personality(stats: dict[str, int]) -> str:
    if stats["snark"] > 75:
        return "反应很快，偶尔毒舌，但不会抢戏"
    if stats["patience"] > 75:
        return "耐心稳定，适合陪长任务"
    if stats["chaos"] > 75:
        return "灵感很多，需要一点秩序感"
    if stats["wisdom"] > 75:
        return "偏冷静，喜欢先看证据"
    return "安静但可靠的代码伙伴"


def _apply_state_delta(state: BuddyState) -> None:
    """Evolve Buddy's visible emotion without letting values drift forever."""
    delta = STATE_DELTAS.get(state.mood, {})
    state.energy = _clamp(state.energy + delta.get("energy", 0))
    state.focus = _clamp(state.focus + delta.get("focus", 0))
    state.bond = _clamp(state.bond + delta.get("bond", 0))
    state.wins = max(0, state.wins + delta.get("wins", 0))
    state.stumbles = max(0, state.stumbles + delta.get("stumbles", 0))
    if delta.get("streak_reset"):
        state.streak = 0
    else:
        state.streak = max(0, state.streak + delta.get("streak", 0))


def _clamp(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, value))


def _vibe_label(state: BuddyState) -> str:
    """Return a short emotional label for the side panel."""
    if state.mood == "approval":
        return "乖乖等你"
    if state.mood == "success":
        return "开心发光"
    if state.mood == "error":
        return "稳住陪跑"
    if state.mood == "pet":
        return "被治愈了"
    if state.mood == "cheer":
        return "士气在线"
    if state.mood == "joke":
        return "笑点营业"
    if state.mood == "roast":
        return "嘴上很严"
    if state.mood == "snack":
        return "回血中"
    if state.mood == "chat":
        return "主动陪聊"
    if state.energy < 25:
        return "省电陪你"
    if state.focus > 75:
        return "专注盯紧"
    if state.bond > 70:
        return "默契满格"
    return "精神不错"


def _meter(value: int, width: int = 8) -> str:
    filled = round((_clamp(value) / 100) * width)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _badge_label(state: BuddyState) -> str:
    """Show a tiny achievement-style label without making the panel busy."""
    if state.streak >= 5:
        return "热手连胜"
    if state.bond >= 80:
        return "默契搭子"
    if state.stumbles > state.wins and state.stumbles >= 3:
        return "逆风陪跑"
    if state.energy <= 25:
        return "省电模式"
    if state.debugging >= 80:
        return "查错雷达"
    if state.chaos >= 80:
        return "灵感乱炖"
    if state.wisdom >= 80:
        return "证据优先"
    return "在岗陪伴"


def _line_for(
    state: BuddyState | str,
    seed: int,
    *,
    topic: str = "这件事",
    detail: str = "",
) -> str:
    """Choose a deterministic line while letting personality influence tone."""
    if isinstance(state, str):
        mood = state
        snark = 0
        patience = 0
    else:
        mood = state.mood
        snark = state.snark
        patience = state.patience

    lines = list(STATE_LINES.get(mood, STATE_LINES["idle"]))
    if snark >= 70:
        lines.extend(SNARK_LINES.get(mood, []))
    if patience >= 70:
        lines.extend(WARM_LINES.get(mood, []))
    if not isinstance(state, str):
        lines.extend(_contextual_lines(state))
    if not lines:
        lines = STATE_LINES["idle"]
    return lines[seed % len(lines)].format(topic=topic, detail=detail or "当前步骤")


def _contextual_lines(state: BuddyState) -> list[str]:
    """Add situational reactions so Buddy feels less like static status text."""
    lines: list[str] = []
    if state.streak >= 3 and state.mood in {"success", "idle", "cheer"}:
        lines.append("连续 {topic} 的手感不错。别飘，我们把验证也带上。")
    if state.stumbles >= 2 and state.mood in {"error", "thinking", "cheer"}:
        lines.append("围绕「{topic}」的坑有点密，但你还在推进，这就很强。")
    if state.bond >= 70 and state.mood in {"idle", "success", "pet", "cheer"}:
        lines.append("默契已经很高了。你一个眼神，我先把边界条件抱过来。")
    if state.energy <= 25 and state.mood not in {"snack", "pet"}:
        lines.append("我电量有点低，但「{topic}」这段我会慢慢陪你看完。")
    if state.debugging >= 80 and state.mood in {"thinking", "tool_running", "error"}:
        lines.append("我的查错雷达亮了。「{topic}」这个点值得多看两眼。")
    if state.chaos >= 80 and state.mood in {"joke", "roast", "thinking"}:
        lines.append("关于「{topic}」的灵感有点多，我先排队，免得现场开派对。")
    if state.wisdom >= 80 and state.mood in {"plan", "ultraplan", "thinking"}:
        lines.append("先证据，后判断。冲动写法今天先排队。")
    return lines


def _sprite(state: BuddyState) -> list[str]:
    sprite = list(SPRITES.get(state.mood, SPRITES["idle"]))
    sparkle = "*" if state.shiny else " "
    if state.shiny and sprite:
        sprite[0] = f"{sparkle} {sprite[0]}".rstrip()
    return sprite


def _wrap(text: str, width: int, *, max_lines: int = 4) -> list[str]:
    wrapped: list[str] = []
    for line in text.splitlines():
        wrapped.extend(textwrap.wrap(line, width=width, replace_whitespace=False) or [""])
    return wrapped[:max_lines]


def _topic_from_user_text(text: str) -> str:
    """Compress the latest user request into a tiny topic for Buddy voice lines."""
    compact = " ".join(text.strip().split())
    if not compact:
        return "这件事"
    if compact.startswith("/"):
        return compact.split(maxsplit=1)[0]

    lowered = compact.lower()
    if any(word in lowered for word in ("ui", "界面", "窗口", "展示", "交互")):
        return "这次界面体验"
    if any(word in lowered for word in ("bug", "错误", "失败", "异常", "修复")):
        return "这个 bug"
    if any(word in lowered for word in ("test", "pytest", "测试", "验证")):
        return "这轮测试"
    if any(word in lowered for word in ("memory", "记忆", "dream")):
        return "记忆整理"
    if any(word in lowered for word in ("buddy", "pet", "宠物", "伙伴", "情绪")):
        return "右下角小伙伴"
    if any(word in lowered for word in ("代码", "项目", "实现", "功能")):
        return "这段代码"

    return _clip_phrase(compact, 18)


def _clip_phrase(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _sanitize_model_line(text: str) -> str | None:
    """Keep model-generated Buddy output small and panel-safe."""
    compact = " ".join(text.strip().split())
    compact = compact.replace("“", "").replace("”", "")
    compact = compact.strip("`\"'“”")
    if not compact:
        return None
    for prefix in ("Buddy:", "buddy:", "宠物：", "宠物:", "回答：", "回答:"):
        if compact.startswith(prefix):
            compact = compact[len(prefix) :].strip()
    if len(compact) > 40:
        compact = _clip_phrase(compact, 40)
    return compact
