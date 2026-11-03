import asyncio
import json
import re
from pathlib import Path
from typing import Any

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .render import (
    render_equip_card,
    render_hero_card,
    render_lineup_detail,
    render_lineup_list,
    render_trait_card,
)

TFT_JS = "https://game.gtimg.cn/images/lol/act/img/tft/js"
LINEUP_BASE = "https://game.gtimg.cn/images/lol/act/tftzlkauto/json/lineupJson"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

QUALITY_COLORS = {"SS": (240, 169, 59), "S": (199, 125, 232), "A": (90, 167, 232)}

STATIC_TTL = 12 * 3600
LINEUP_TTL = 6 * 3600

TAG_RE = re.compile(r"<[^>]+>")
BR_RE = re.compile(r"<br\s*/?>", re.I)
KEYWORD_RE = re.compile(r"\{\{TFT_Keyword_(\w+)\}\}")
KEYWORD_MAP = {
    "Burn": "烧伤",
    "Chill": "寒冷",
    "Precision": "精准",
    "Wound": "重伤",
}
PLACEHOLDER_MAP = {
    "scaleHealth": "生命值",
    "scaleAP": "法术强度",
    "scaleAttackDamage": "攻击力",
    "scaleArmor": "护甲",
    "scaleMagicResist": "魔法抗性",
    "scaleAttackSpeed": "攻击速度",
    "scaleMana": "法力值",
    "scaleCritDamage": "暴击伤害",
    "scaleCritChance": "暴击几率",
}
PLACEHOLDER_RE = re.compile(r"%i:([a-zA-Z]+)(?:\[([^\]]+)\])?%")


@register(
    "astrbot_plugin_yunding",
    "Matsuko",
    "云顶之弈查询：官方阵容库推荐、弈子详情、装备合成、羁绊效果（数据来自腾讯官方资料库）",
    "1.0.0",
)
class YundingPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = (
            Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_yunding"
        )
        (self.data_dir / "icons").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "cards").mkdir(exist_ok=True)
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[str, tuple[float, Any]] = {}

    async def terminate(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ---------------- http ----------------

    def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=int(self.config.get("timeout", 25)))
            )
        return self._session

    async def _cache_get(self, key: str, ttl: float):
        hit = self._cache.get(key)
        if hit and hit[0] > asyncio.get_event_loop().time():
            return hit[1]
        return None

    def _cache_set(self, key: str, value: Any, ttl: float):
        self._cache[key] = (asyncio.get_event_loop().time() + ttl, value)

    async def _fetch_json(self, url: str) -> Any:
        async with self._session_get().get(url, headers={"User-Agent": UA}) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def _download_icon(self, url: str) -> Path | None:
        if not url:
            return None
        name = url.rsplit("/", 1)[-1]
        path = self.data_dir / "icons" / name
        if path.exists() and path.stat().st_size > 0:
            return path
        try:
            async with self._session_get().get(url, headers={"User-Agent": UA}) as resp:
                if resp.status != 200:
                    return None
                path.write_bytes(await resp.read())
            return path
        except Exception as e:
            logger.warning(f"yunding: icon download failed {url}: {e}")
            return None

    # ---------------- static data ----------------

    async def _get_static(self, name: str) -> dict:
        """Fetch chess/equip/race/job data. Each returns {'season', 'data': [...]}."""
        cached = await self._cache_get(f"static_{name}", STATIC_TTL)
        if cached is not None:
            return cached
        data = await self._fetch_json(f"{TFT_JS}/{name}.js")
        self._cache_set(f"static_{name}", data, STATIC_TTL)
        return data

    def _static_list(self, name: str) -> list[dict]:
        raw = self._cache.get(f"static_{name}")
        if not raw:
            return []
        data = raw[1].get("data", [])
        return data if isinstance(data, list) else []

    def _get_season_label(self) -> str:
        raw = self._cache.get("static_chess")
        if raw:
            return str(raw[1].get("season", "") or raw[1].get("version", ""))
        return ""

    async def _get_lineups(self) -> list[dict]:
        """Fetch the current lineup database, probing patch subdirs from high to low."""
        cached = await self._cache_get("lineups", LINEUP_TTL)
        if cached is not None:
            return cached
        await self._get_static("chess")
        override = str(self.config.get("lineup_path", "") or "").strip().strip("/")
        season = re.search(r"S(\d+)", self._get_season_label())
        sn = season.group(1) if season else "18"
        candidates = (
            [override] if override else [f"s{sn}/{v}" for v in (6, 5, 4, 3, 2, 1)]
        )
        for sub in candidates:
            url = f"{LINEUP_BASE}/{sub}/lineup_detail_total.json"
            try:
                data = await self._fetch_json(url)
            except Exception:
                continue
            items = data.get("lineup_list") if isinstance(data, dict) else data
            if items:
                self._cache_set("lineups", items, LINEUP_TTL)
                return items
        raise RuntimeError("阵容库数据暂不可用")

    # ---------------- index maps ----------------

    async def _chess_map(self) -> dict[str, dict]:
        """Index chess entries by hero_EN_name (lineup join key) and its normalized form."""
        cached = await self._cache_get("chess_map", STATIC_TTL)
        if cached is not None:
            return cached
        await self._get_static("chess")
        mapping: dict[str, dict] = {}
        for e in self._static_list("chess"):
            en = str(e.get("hero_EN_name", "")).strip()
            if en:
                mapping.setdefault(en, e)
                mapping.setdefault(self._hero_key(en), e)
        self._cache_set("chess_map", mapping, STATIC_TTL)
        return mapping

    def _avatar_url(self, chess_id) -> str:
        """Build the champion portrait URL from the chess id (e.g. 100367)."""
        return f"https://game.gtimg.cn/images/lol/act/img/tft/champions/{chess_id}.png"

    async def _equip_map(self) -> dict[str, dict]:
        """englishName (e.g. 'DA_Component_RecurveBow') -> equip entry."""
        cached = await self._cache_get("equip_map", STATIC_TTL)
        if cached is not None:
            return cached
        await self._get_static("equip")
        mapping = {}
        for e in self._static_list("equip"):
            for k in (e.get("englishName"), e.get("equipId")):
                if k:
                    mapping.setdefault(str(k), e)
        self._cache_set("equip_map", mapping, STATIC_TTL)
        return mapping

    async def _trait_map(self) -> dict[str, dict]:
        """Index races and jobs by every plausible id key."""
        cached = await self._cache_get("trait_map", STATIC_TTL)
        if cached is not None:
            return cached
        for name in ("race", "job"):
            await self._get_static(name)
        mapping = {}
        for name in ("race", "job"):
            for e in self._static_list(name):
                for k in (
                    e.get("raceId"),
                    e.get("jobId"),
                    e.get("traitId"),
                    e.get("characterid"),
                ):
                    if k:
                        mapping.setdefault(str(k), e)
        self._cache_set("trait_map", mapping, STATIC_TTL)
        return mapping

    @staticmethod
    def _hero_key(hero_id: str) -> str:
        k = hero_id.strip()
        if k.lower().startswith("da_"):
            k = k[3:]
        if re.match(r"^\d+_", k):
            k = k.split("_", 1)[1]
        if k.lower().endswith("18"):
            k = k[:-2]
        return k.lower()

    async def _resolve_unit(self, hero_entry: dict, chess: dict[str, dict]) -> dict:
        """Resolve a lineup hero entry into a display unit."""
        hid = str(hero_entry.get("hero_id", ""))
        chess_e = chess.get(hid) or chess.get(self._hero_key(hid))
        equip_ids = [
            x for x in str(hero_entry.get("equipment_id", "")).split(",") if x.strip()
        ]
        return {
            "hero_id": hid,
            "name": (chess_e or {}).get("displayName", hid),
            "chess_id": (chess_e or {}).get("chessId", ""),
            "price": int((chess_e or {}).get("price", 1) or 1),
            "star": int(hero_entry.get("numStar", 1) or 1),
            "carry": bool(hero_entry.get("is_carry_hero")),
            "equip_ids": equip_ids,
            "icon_url": self._avatar_url((chess_e or {}).get("chessId", "")),
        }

    @staticmethod
    def _clean_text(text: str) -> str:
        text = BR_RE.sub("\n", str(text))
        text = TAG_RE.sub("", text)
        text = KEYWORD_RE.sub(lambda m: KEYWORD_MAP.get(m.group(1), m.group(1)), text)
        text = text.replace("\\n", "\n")

        def repl(m: re.Match) -> str:
            base = PLACEHOLDER_MAP.get(m.group(1), "")
            if not base:
                return "X"
            return f"{base}{m.group(2)}" if m.group(2) else base

        return PLACEHOLDER_RE.sub(repl, text).replace("\r\n", "\n").strip()

    async def _unit_traits(self, chess_e: dict, traits: dict[str, dict]) -> list[str]:
        names = []
        for field in ("raceIds", "jobIds"):
            for tid in str(chess_e.get(field, "")).split(","):
                tid = tid.strip()
                if not tid:
                    continue
                t = traits.get(tid)
                if t and t.get("name"):
                    if t["name"] not in names:
                        names.append(t["name"])
                    continue
                # Unresolvable trait id: fall back to a readable suffix.
                m = re.search(r"DA(?:_\d+)?_([A-Za-z]+?)(?:18)?(?:_UniqueTrait)?$", tid)
                if m and m.group(1) not in names:
                    names.append(m.group(1))
        return names

    async def _equip_entries(
        self, ids: list[str], equip: dict[str, dict]
    ) -> list[dict]:
        out = []
        for i in ids:
            e = equip.get(i)
            if e:
                out.append(e)
        return out

    # ---------------- commands ----------------

    @filter.command("云顶", alias={"云顶之弈"})
    async def yunding(self, event: AstrMessageEvent, query: str = ""):
        """云顶阵容：/云顶 看推荐阵容；/云顶 巨龙 看阵容详情"""
        try:
            if query in ("帮助", "help"):
                yield event.plain_result(self._help_text())
                return
            lineups = await self._get_lineups()
            if not query:
                yield event.chain_result(
                    [Image.fromFileSystem(str(await self._lineup_list_card()))]
                )
                return
            comp = self._find_lineup(lineups, query)
            if not comp:
                names = "、".join(self._lineup_name(x) for x in lineups[:8])
                yield event.plain_result(
                    f"没找到阵容「{query}」。当前的阵容有：{names} 等，试试其中的关键词"
                )
                return
            yield event.chain_result(
                [Image.fromFileSystem(str(await self._lineup_detail_card(comp)))]
            )
        except Exception as e:
            logger.error(f"yunding: {self._exc()}")
            yield event.plain_result(f"云顶数据查询失败了：{e}")

    @filter.command("云顶阵容")
    async def yunding_lineup(self, event: AstrMessageEvent, query: str = ""):
        """阵容查询：/云顶阵容 看列表；/云顶阵容 巨龙 看详情"""
        await self._dispatch_lineup(event, query)

    @filter.command("云顶英雄")
    async def yunding_hero(self, event: AstrMessageEvent, query: str = ""):
        """弈子查询：/云顶英雄 茂凯"""
        try:
            if not query:
                yield event.plain_result("用法：/云顶英雄 弈子名，例如 /云顶英雄 茂凯")
                return
            chess = await self._get_static("chess")
            hit = self._find_chess(chess.get("data", []), query)
            if not hit:
                yield event.plain_result(
                    f"没找到弈子「{query}」，试试当前赛季的名字（赛季 {self._get_season_label()}）"
                )
                return
            yield event.chain_result(
                [Image.fromFileSystem(str(await self._hero_card(hit)))]
            )
        except Exception as e:
            logger.error(f"yunding hero: {self._exc()}")
            yield event.plain_result(f"云顶数据查询失败了：{e}")

    @filter.command("云顶装备")
    async def yunding_equip(self, event: AstrMessageEvent, query: str = ""):
        """装备查询：/云顶装备 无尽之刃"""
        try:
            if not query:
                yield event.plain_result(
                    "用法：/云顶装备 装备名，例如 /云顶装备 无尽之刃"
                )
                return
            equip = await self._get_static("equip")
            hit = self._find_equip(equip.get("data", []), query)
            if not hit:
                yield event.plain_result(f"没找到装备「{query}」")
                return
            yield event.chain_result(
                [Image.fromFileSystem(str(await self._equip_card(hit)))]
            )
        except Exception as e:
            logger.error(f"yunding equip: {self._exc()}")
            yield event.plain_result(f"云顶数据查询失败了：{e}")

    @filter.command("云顶羁绊")
    async def yunding_trait(self, event: AstrMessageEvent, query: str = ""):
        """羁绊查询：/云顶羁绊 魔战士"""
        try:
            if not query:
                yield event.plain_result(
                    "用法：/云顶羁绊 羁绊名，例如 /云顶羁绊 魔战士"
                )
                return
            hit = await self._find_trait(query)
            if not hit:
                yield event.plain_result(
                    f"没找到羁绊「{query}」，当前赛季（{self._get_season_label()}）可能没有这个羁绊"
                )
                return
            yield event.chain_result(
                [Image.fromFileSystem(str(await self._trait_card(hit)))]
            )
        except Exception as e:
            logger.error(f"yunding trait: {self._exc()}")
            yield event.plain_result(f"云顶数据查询失败了：{e}")

    @filter.command("云顶帮助")
    async def yunding_help(self, event: AstrMessageEvent):
        yield event.plain_result(self._help_text())

    # ---------------- llm tool ----------------

    @filter.llm_tool(name="yunding_query")
    async def yunding_query(
        self, event: AstrMessageEvent, action: str, keyword: str = ""
    ) -> str:
        """查询云顶之弈（TFT）数据。action 取值: "lineups"=当前版本推荐阵容列表; "lineup"=按关键词查阵容详情（构成/装备/羁绊/运营）; "hero"=按名字查弈子费用/羁绊/技能; "equip"=查装备效果与合成; "trait"=查羁绊各级效果。
        Args:
            action: 查询类型，可选 lineups / lineup / hero / equip / trait
            keyword: 阵容、弈子、装备或羁绊名称；action 为 lineups 时留空
        """
        try:
            if action == "lineups":
                lineups = await self._get_lineups()
                top_n = int(self.config.get("lineup_top_n", 24))
                lines = [f"云顶 {self._get_season_label()} 推荐阵容："]
                for comp in lineups[:top_n]:
                    try:
                        det = json.loads(comp.get("detail", "{}"), strict=False)
                    except Exception:
                        det = {}
                    lines.append(
                        f"- {self._lineup_name(comp)}"
                        f"（质量{comp.get('quality', '?')}，{det.get('needLevel', '?')}级成型）"
                    )
                return "\n".join(lines)
            if action == "lineup":
                lineups = await self._get_lineups()
                comp = self._find_lineup(lineups, keyword)
                if not comp:
                    names = "、".join(self._lineup_name(x) for x in lineups[:8])
                    return f"没找到阵容「{keyword}」。当前阵容：{names} 等"
                det = json.loads(comp.get("detail", "{}"), strict=False)
                chess = await self._chess_map()
                equip = await self._equip_map()
                traits = await self._trait_map()
                unit_lines, trait_count = [], {}
                for h in det.get("hero_location", []):
                    u = await self._resolve_unit(h, chess)
                    e_names = [
                        e.get("name", "")
                        for e in await self._equip_entries(u["equip_ids"], equip)
                    ]
                    star = "★" * u["star"] if u["star"] >= 2 else ""
                    carry = "（C位）" if u["carry"] else ""
                    eq = f" 装备：{'/'.join(e_names)}" if e_names else ""
                    unit_lines.append(f"- {u['name']} {u['price']}费{star}{carry}{eq}")
                    chess_e = chess.get(u["hero_id"]) or chess.get(
                        self._hero_key(u["hero_id"])
                    )
                    if chess_e:
                        for t in await self._unit_traits(chess_e, traits):
                            trait_count[t] = trait_count.get(t, 0) + 1
                chips = "、".join(
                    f"{k}x{v}"
                    for k, v in sorted(trait_count.items(), key=lambda x: -x[1])[:8]
                )
                order = await self._equip_entries(
                    [x for x in str(det.get("equipment_order", "")).split(",") if x],
                    equip,
                )
                parts = [
                    f"【{self._lineup_name(comp)}】质量{comp.get('quality', '')}，"
                    f"{det.get('needLevel', '?')}级成型（中期 {det.get('needLevel_middle', '?')}级）",
                    "构成：\n" + "\n".join(unit_lines),
                    f"羁绊：{chips}",
                ]
                if order:
                    parts.append(
                        "装备优先级：" + " → ".join(e.get("name", "") for e in order)
                    )
                for label, key in (
                    ("海克斯", "hex_info"),
                    ("运营要点", "location_info"),
                ):
                    txt = self._clean_text(det.get(key, ""))
                    if txt:
                        parts.append(f"{label}：{txt[:180]}")
                return "\n".join(parts)
            if action == "hero":
                chess = await self._get_static("chess")
                hit = self._find_chess(chess.get("data", []), keyword)
                if not hit:
                    return f"没找到弈子「{keyword}」（当前赛季 {self._get_season_label()}）"
                traits = await self._trait_map()
                tnames = await self._unit_traits(hit, traits)
                skill = self._clean_text(hit.get("skillIntroduce", ""))
                return (
                    f"{hit.get('displayName', keyword)}：{hit.get('price', '?')}费，"
                    f"羁绊 {'/'.join(tnames)}。技能：{hit.get('skillName', '')}——{skill[:150]}"
                )
            if action == "equip":
                equip = await self._get_static("equip")
                hit = self._find_equip(equip.get("data", []), keyword)
                if not hit:
                    return f"没找到装备「{keyword}」"
                des = self._clean_text(hit.get("effect", ""))
                return f"{hit.get('name', keyword)}：{des[:200]}"
            if action == "trait":
                hit = await self._find_trait(keyword)
                if not hit:
                    return f"没找到羁绊「{keyword}」（当前赛季 {self._get_season_label()}）"
                des = self._clean_text(hit.get("introduce", ""))
                return f"羁绊 {hit.get('name', keyword)}：{des[:250]}"
            return "action 只支持 lineups / lineup / hero / equip / trait"
        except Exception as e:
            logger.error(f"yunding llm_tool: {self._exc()}")
            return f"查询失败: {e}"

    async def _dispatch_lineup(self, event: AstrMessageEvent, query: str):
        try:
            lineups = await self._get_lineups()
            if not query:
                yield event.chain_result(
                    [Image.fromFileSystem(str(await self._lineup_list_card()))]
                )
                return
            comp = self._find_lineup(lineups, query)
            if not comp:
                yield event.plain_result(
                    f"没找到阵容「{query}」，发 /云顶 可以看全部阵容名单"
                )
                return
            yield event.chain_result(
                [Image.fromFileSystem(str(await self._lineup_detail_card(comp)))]
            )
        except Exception as e:
            logger.error(f"yunding lineup: {self._exc()}")
            yield event.plain_result(f"云顶数据查询失败了：{e}")

    # ---------------- card builders ----------------

    @staticmethod
    def _help_text() -> str:
        return (
            "云顶之弈查询（数据来自腾讯官方资料库）：\n"
            "/云顶 —— 当前版本推荐阵容列表\n"
            "/云顶 巨龙 —— 查阵容详情（阵容/装备/运营思路）\n"
            "/云顶英雄 茂凯 —— 弈子技能、费用、属性\n"
            "/云顶装备 无尽之刃 —— 装备效果与合成\n"
            "/云顶羁绊 魔战士 —— 羁绊各级效果"
        )

    @staticmethod
    def _lineup_name(comp: dict) -> str:
        try:
            det = json.loads(comp.get("detail", "{}"), strict=False)
            return str(det.get("line_name", "")).strip()
        except Exception:
            return ""

    @staticmethod
    def _find_lineup(lineups: list[dict], query: str) -> dict | None:
        q = query.strip().lower()
        for comp in lineups:
            name = YundingPlugin._lineup_name(comp)
            if q in name.lower():
                return comp
        for comp in lineups:
            author = str(comp.get("lineupauthor_data", {}).get("name", "")).lower()
            if q in author:
                return comp
        return None

    @staticmethod
    def _find_chess(units: list[dict], query: str) -> dict | None:
        q = query.strip().lower()
        for e in units:
            if q == str(e.get("displayName", "")).lower():
                return e
        for e in units:
            if q in str(e.get("displayName", "")):
                return e
        return None

    @staticmethod
    def _find_equip(items: list[dict], query: str) -> dict | None:
        q = query.strip()
        for e in items:
            if q == e.get("name", ""):
                return e
        for e in items:
            if q in str(e.get("name", "")) or q in str(e.get("keywords", "")):
                return e
        return None

    async def _find_trait(self, query: str) -> dict | None:
        traits = await self._trait_map()
        q = query.strip()
        seen: dict[int, dict] = {}
        for e in traits.values():
            if q == e.get("name", ""):
                seen[id(e)] = e
        if not seen:
            for e in traits.values():
                if q in str(e.get("name", "")):
                    seen[id(e)] = e
        return next(iter(seen.values()), None)

    async def _lineup_list_card(self) -> Path:
        lineups = await self._get_lineups()
        top_n = int(self.config.get("lineup_top_n", 24))
        rows = []
        for comp in lineups[:top_n]:
            try:
                det = json.loads(comp.get("detail", "{}"), strict=False)
            except Exception:
                det = {}
            rows.append(
                {
                    "quality": str(comp.get("quality", "")),
                    "name": self._lineup_name(comp),
                    "author": str(comp.get("lineupauthor_data", {}).get("name", "")),
                    "level": f"{det.get('needLevel', '?')}费",
                    "time": str(comp.get("rel_time", ""))[:10],
                }
            )
        return render_lineup_list(
            rows,
            self._get_season_label(),
            self.data_dir / "cards" / "lineups.png",
        )

    async def _lineup_detail_card(self, comp: dict) -> Path:
        try:
            det = json.loads(comp.get("detail", "{}"), strict=False)
        except Exception:
            det = {}
        chess = await self._chess_map()
        equip = await self._equip_map()
        traits = await self._trait_map()
        units = [
            await self._resolve_unit(h, chess) for h in det.get("hero_location", [])
        ]
        for u in units:
            chess_e = chess.get(u["hero_id"]) or chess.get(self._hero_key(u["hero_id"]))
            u["traits"] = await self._unit_traits(chess_e, traits) if chess_e else []
            u["icon"] = await self._download_icon(u["icon_url"])
            u["item_icons"] = [
                await self._download_icon(str(e.get("imagePath", "")))
                for e in await self._equip_entries(u["equip_ids"], equip)
            ]
        trait_count: dict[str, int] = {}
        for u in units:
            for tname in u["traits"]:
                trait_count[tname] = trait_count.get(tname, 0) + 1
        trait_chips = sorted(trait_count.items(), key=lambda x: -x[1])[:8]
        texts = {
            "成型等级": f"{det.get('needLevel', '?')} 级（中期 {det.get('needLevel_middle', '?')} 级）",
            "海克斯": self._clean_text(det.get("hex_info", "")),
            "前期": self._clean_text(det.get("early_info", "")),
            "装备": self._clean_text(det.get("equipment_info", "")),
            "站位与操作": self._clean_text(det.get("location_info", "")),
        }
        order = [
            await self._equip_entries(
                [x for x in str(det.get("equipment_order", "")).split(",") if x],
                equip,
            )
        ]
        order_names = [e.get("name", "") for e in order[0]]
        meta = comp.get("lineupauthor_data", {})
        info = {
            "quality": str(comp.get("quality", "")),
            "author": str(meta.get("name", "")),
            "time": str(comp.get("rel_time", ""))[:10],
            "version": str(
                comp.get("simulator_edition", "") or self._get_season_label()
            ),
            "equipment_order": " → ".join(order_names),
        }
        return render_lineup_detail(
            self._lineup_name(comp),
            info,
            units,
            trait_chips,
            texts,
            self.data_dir / "cards" / f"lineup_{comp.get('id', 'x')}.png",
        )

    async def _hero_card(self, chess_e: dict) -> Path:
        traits = await self._trait_map()
        trait_names = await self._unit_traits(chess_e, traits)
        portrait_url = (
            self._avatar_url(chess_e.get("chessId", ""))
            if chess_e.get("chessId")
            else str(chess_e.get("imagePath", "") or "")
        )
        portrait = await self._download_icon(portrait_url)
        stat_fields = [
            ("生命", "life"),
            ("攻击", "attack"),
            ("攻速", "attackSpeed"),
            ("护甲", "armor"),
            ("魔抗", "magicResist"),
            ("初始法力", "startMagic"),
        ]
        stats = []
        for label, key in stat_fields:
            try:
                v = float(chess_e.get(key, 0) or 0)
            except (TypeError, ValueError):
                v = 0
            if v > 0:
                stats.append((label, f"{v:g}"))
        skill = self._clean_text(chess_e.get("skillIntroduce", ""))
        return render_hero_card(
            chess_e,
            trait_names,
            skill,
            stats,
            portrait,
            self._get_season_label(),
            self.data_dir / "cards" / f"chess_{chess_e.get('chessId', 'x')}.png",
        )

    async def _equip_card(self, equip_e: dict) -> Path:
        equip = await self._equip_map()
        formula = await self._equip_entries(
            [
                x.strip()
                for x in str(equip_e.get("formula", "")).split(",")
                if x.strip()
            ],
            equip,
        )
        formula_icons = [
            await self._download_icon(str(e.get("imagePath", ""))) for e in formula
        ]
        icon = await self._download_icon(str(equip_e.get("imagePath", "")))
        effect = self._clean_text(equip_e.get("effect", "")).replace(";", "\n")
        return render_equip_card(
            equip_e,
            effect,
            formula,
            formula_icons,
            icon,
            self._get_season_label(),
            self.data_dir / "cards" / f"equip_{equip_e.get('equipId', 'x')}.png",
        )

    async def _trait_card(self, trait_e: dict) -> Path:
        icon = await self._download_icon(str(trait_e.get("imagePath", "")))
        return render_trait_card(
            trait_e,
            icon,
            self._get_season_label(),
            self.data_dir / "cards" / f"trait_{trait_e.get('traitId', 'x')}.png",
        )

    @staticmethod
    def _exc() -> str:
        import traceback

        return traceback.format_exc()
