"""PIL-based card rendering for the yunding (TFT) plugin."""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path(__file__).parent / "fonts"
FONT_REG = str(FONT_DIR / "NotoSansHans-Regular.otf")
FONT_MED = str(FONT_DIR / "NotoSansHans-Medium.otf")

BG = (10, 14, 20)
PANEL = (19, 26, 36)
PANEL_LIGHT = (28, 37, 50)
GOLD = (245, 210, 122)
TEXT = (232, 232, 232)
DIM = (143, 150, 160)

QUALITY_COLORS = {"SS": (240, 169, 59), "S": (199, 125, 232), "A": (90, 167, 232)}
# TFT unit cost colors: 1 gray, 2 green, 3 blue, 4 purple, 5 gold.
COST_COLORS = {
    1: (170, 180, 195),
    2: (91, 201, 122),
    3: (90, 167, 232),
    4: (199, 125, 232),
    5: (240, 169, 59),
}

CARD_W = 760


def _font(size: int, medium: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_MED if medium else FONT_REG, size)


def _wrap(text: str, f: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines = []
    for raw in text.split("\n"):
        if not raw:
            lines.append("")
            continue
        cur = ""
        for ch in raw:
            if f.getlength(cur + ch) > max_w:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        lines.append(cur)
    return lines


def _wrapped_height(
    text: str, f: ImageFont.FreeTypeFont, max_w: int, line_h: int
) -> int:
    return len(_wrap(text, f, max_w)) * line_h


def _draw_wrapped(
    d: ImageDraw.ImageDraw, x: int, y: int, text: str, f, color, max_w: int, line_h: int
) -> int:
    for i, line in enumerate(_wrap(text, f, max_w)):
        d.text((x, y + i * line_h), line, font=f, fill=color)
    return len(_wrap(text, f, max_w)) * line_h


def _new_canvas(width: int, height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (width, height), BG)
    return img, ImageDraw.Draw(img)


def _finish(img: Image.Image, out_path: Path) -> Path:
    img.save(out_path, "PNG")
    return out_path


def _placeholder_icon(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size), PANEL_LIGHT)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, size - 1, size - 1], outline=PANEL, width=2)
    return img


def _load_icon(path: Path | None, size: int) -> Image.Image:
    if path and Path(path).exists():
        try:
            return Image.open(path).convert("RGB").resize((size, size))
        except Exception:
            pass
    return _placeholder_icon(size)


def _paste_icon(
    img: Image.Image, icon: Image.Image, x: int, y: int, size: int, border=(60, 70, 85)
):
    img.paste(icon, (x, y))
    ImageDraw.Draw(img).rectangle(
        [x, y, x + size - 1, y + size - 1], outline=border, width=1
    )


def _badge(
    d: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    color,
    w: int = 40,
    h: int = 26,
    size: int = 16,
) -> int:
    d.rounded_rectangle([x, y, x + w, y + h], radius=4, fill=color)
    f = _font(size, medium=True)
    tw = d.textlength(text, font=f)
    d.text((x + (w - tw) / 2, y + (h - size) / 2 - 1), text, font=f, fill=(15, 18, 24))
    return w


def _header(img: Image.Image, d: ImageDraw.ImageDraw, title: str, subtitle: str):
    d.rectangle([0, 0, img.width, 64], fill=PANEL)
    f = _font(24, medium=True)
    d.text((24, 18), title, font=f, fill=GOLD)
    f2 = _font(16)
    tw = d.textlength(subtitle, font=f2)
    d.text((img.width - tw - 24, 24), subtitle, font=f2, fill=DIM)


def _footer(d: ImageDraw.ImageDraw, img: Image.Image, text: str):
    f = _font(16)
    tw = d.textlength(text, font=f)
    d.text(((img.width - tw) / 2, img.height - 30), text, font=f, fill=DIM)


# ---------------- lineup list ----------------


def render_lineup_list(rows: list[dict], season: str, out: Path) -> Path:
    row_h = 58
    height = 64 + 8 + len(rows) * row_h + 46
    img, d = _new_canvas(CARD_W, height)
    _header(img, d, "云顶之弈 · 推荐阵容", f"{season} · 共 {len(rows)} 套")
    y = 72
    for i, r in enumerate(rows):
        if i % 2 == 0:
            d.rectangle([0, y, CARD_W, y + row_h], fill=PANEL)
        color = QUALITY_COLORS.get(r["quality"], (90, 167, 232))
        qx = 16
        qw = 44 if r["quality"] == "SS" else 34
        d.rounded_rectangle([qx, y + 15, qx + qw, y + 41], radius=4, fill=color)
        f_b = _font(17, medium=True)
        tw = d.textlength(r["quality"], font=f_b)
        d.text((qx + (qw - tw) / 2, y + 20), r["quality"], font=f_b, fill=(15, 18, 24))
        f_name = _font(19, medium=True)
        d.text((qx + qw + 14, y + 9), r["name"], font=f_name, fill=TEXT)
        f_dim = _font(15)
        d.text(
            (qx + qw + 14, y + 34),
            f"{r['author']} · {r['level']}",
            font=f_dim,
            fill=DIM,
        )
        t = r["time"]
        tw = d.textlength(t, font=f_dim)
        d.text((CARD_W - tw - 16, y + 20), t, font=f_dim, fill=DIM)
        y += row_h
    _footer(d, img, "数据来自腾讯官方阵容库 · /云顶阵容 关键词 查看详情")
    return _finish(img, out)


# ---------------- lineup detail ----------------


def render_lineup_detail(
    title: str,
    info: dict,
    units: list[dict],
    trait_chips: list[tuple[str, int]],
    texts: dict[str, str],
    out: Path,
) -> Path:
    pad = 24
    text_w = CARD_W - pad * 2
    # Pre-measure board and text sections to size the canvas.
    unit_size = 76
    item_size = 26
    per_row = 8
    unit_rows = max(1, math.ceil(len(units) / per_row))
    board_h = unit_rows * (unit_size + item_size + 22) + 12
    f_body = _font(18)
    sections = []
    for k, v in texts.items():
        if v:
            h = 34 + _wrapped_height(v, f_body, text_w - 8, 27) + 14
            sections.append((k, v, h))
    height = 64 + 56 + board_h + (52 if trait_chips else 0)
    height += (
        sum(h for _, _, h in sections) + (14 if info.get("equipment_order") else 0) + 60
    )
    img, d = _new_canvas(CARD_W, height)
    _header(
        img, d, title[:30], f"{info.get('quality', '')} · {info.get('version', '')}"
    )

    y = 78
    f_meta = _font(17)
    d.text(
        (pad, y),
        f"作者：{info.get('author', '?')}   更新：{info.get('time', '')}",
        font=f_meta,
        fill=DIM,
    )
    y += 34

    # Board: units with cost-colored borders, carry star, item icons below.
    x = pad
    row_y = y
    for idx, u in enumerate(units):
        if idx and idx % per_row == 0:
            row_y += unit_size + item_size + 22
            x = pad
        color = COST_COLORS.get(u["price"], COST_COLORS[1])
        icon = _load_icon(u.get("icon"), unit_size)
        _paste_icon(img, icon, x, row_y, unit_size, border=color)
        # Cost badge (top-left, prominent) and star/carry badges (top-right).
        cb = str(u["price"])
        f_cb = _font(17, medium=True)
        d.rectangle([x, row_y, x + 26, row_y + 26], fill=color)
        tw = d.textlength(cb, font=f_cb)
        d.text((x + (26 - tw) / 2, row_y + 3), cb, font=f_cb, fill=(15, 18, 24))
        # Only mark stars for 2+ (the lineup data marks 1 for units that stay one-star);
        # "C" marks the carry.
        marks = ("★" * min(u["star"], 3) if u["star"] > 1 else "") + (
            "C" if u["carry"] else ""
        )
        if marks:
            f_mk = _font(14, medium=True)
            d.text(
                (x + unit_size - d.textlength(marks, font=f_mk) - 4, row_y + 2),
                marks,
                font=f_mk,
                fill=GOLD if u["carry"] else (255, 255, 255),
            )
        # Name and item icons below the avatar.
        f_nm = _font(14)
        nm = u["name"][:5]
        tw = d.textlength(nm, font=f_nm)
        d.text(
            (x + (unit_size - tw) / 2, row_y + unit_size + 2), nm, font=f_nm, fill=TEXT
        )
        for j, ic in enumerate(u.get("item_icons", [])[:3]):
            item = _load_icon(ic, item_size)
            _paste_icon(
                img, item, x + j * (item_size + 4), row_y + unit_size + 20, item_size
            )
        x += unit_size + 12
    y = row_y + unit_size + item_size + 26

    if trait_chips:
        f_t = _font(16, medium=True)
        tx = pad
        for tname, cnt in trait_chips:
            label = f"{tname} x{cnt}"
            tw = d.textlength(label, font=f_t)
            if tx + tw + 20 > CARD_W - pad:
                tx = pad
                y += 34
            d.rounded_rectangle(
                [tx, y, tx + tw + 16, y + 26], radius=4, fill=PANEL_LIGHT
            )
            d.text((tx + 8, y + 4), label, font=f_t, fill=GOLD)
            tx += tw + 28
        y += 40

    for k, v, h in sections:
        d.rounded_rectangle([pad, y, CARD_W - pad, y + h - 4], radius=6, fill=PANEL)
        f_t = _font(17, medium=True)
        d.text((pad + 14, y + 10), k, font=f_t, fill=GOLD)
        _draw_wrapped(d, pad + 14, y + 38, v, f_body, TEXT, text_w - 28, 27)
        y += h
    if info.get("equipment_order"):
        d.rounded_rectangle([pad, y, CARD_W - pad, y + 44], radius=6, fill=PANEL)
        f_t = _font(16, medium=True)
        d.text(
            (pad + 14, y + 12),
            f"装备优先级：{info['equipment_order']}",
            font=f_t,
            fill=TEXT,
        )
        y += 50

    _footer(d, img, "数据来自腾讯官方阵容库，仅供参考")
    return _finish(img, out)


# ---------------- hero card ----------------


def render_hero_card(
    chess_e: dict,
    trait_names: list[str],
    skill: str,
    stats: list[tuple[str, str]],
    portrait: Path | None,
    season: str,
    out: Path,
) -> Path:
    pad = 24
    text_w = CARD_W - pad * 2
    f_body = _font(18)
    f_title = _font(26, medium=True)
    skill_h = _wrapped_height(skill, f_body, text_w - 120, 27) if skill else 0
    height = 64 + 150 + 40 + skill_h + len(stats) * 0 + 76
    img, d = _new_canvas(CARD_W, height)
    _header(img, d, "云顶之弈 · 弈子", season)

    price = int(chess_e.get("price", 1) or 1)
    color = COST_COLORS.get(price, COST_COLORS[1])
    icon = _load_icon(portrait, 110)
    _paste_icon(img, icon, pad, 84, 110, border=color)
    x = pad + 126
    d.text((x, 84), str(chess_e.get("displayName", "?")), font=f_title, fill=TEXT)
    stars = "★" * price
    f_st = _font(20)
    d.text((x, 122), stars, font=f_st, fill=color)
    d.text(
        (x + d.textlength(stars, font=f_st) + 12, 124),
        f"{price} 费",
        font=_font(17),
        fill=DIM,
    )
    # Trait chips.
    tx = x
    ty = 156
    f_t = _font(15, medium=True)
    for tname in trait_names[:4]:
        tw = d.textlength(tname, font=f_t)
        if tx + tw + 16 > CARD_W - pad:
            break
        d.rounded_rectangle([tx, ty, tx + tw + 14, ty + 24], radius=4, fill=PANEL_LIGHT)
        d.text((tx + 7, ty + 3), tname, font=f_t, fill=GOLD)
        tx += tw + 24

    y = 84 + 110 + 14
    if skill:
        d.text(
            (pad, y),
            f"技能 · {chess_e.get('skillName', '')}（{chess_e.get('skillType', '')}）",
            font=_font(18, medium=True),
            fill=GOLD,
        )
        y += 32
        y += _draw_wrapped(d, pad, y, skill, f_body, TEXT, text_w, 27)
    if stats:
        y += 16
        f_s = _font(17, medium=True)
        sx = pad
        for label, v in stats:
            seg = f"{label} {v}"
            d.text((sx, y), seg, font=f_s, fill=TEXT)
            sx += d.textlength(seg, font=f_s) + 28
        y += 30
    _footer(d, img, "数据来自腾讯官方资料库")
    return _finish(img, out)


# ---------------- equip card ----------------


def render_equip_card(
    equip_e: dict,
    effect: str,
    formula: list[dict],
    formula_icons: list[Path | None],
    icon: Path | None,
    season: str,
    out: Path,
) -> Path:
    pad = 24
    text_w = CARD_W - pad * 2
    f_body = _font(18)
    height = 64 + 150 + 30 + _wrapped_height(effect, f_body, text_w, 27) + 60
    img, d = _new_canvas(CARD_W, height)
    _header(img, d, "云顶之弈 · 装备", season)

    icon_img = _load_icon(icon, 110)
    _paste_icon(img, icon_img, pad, 84, 110, border=(90, 167, 232))
    x = pad + 126
    d.text(
        (x, 90), str(equip_e.get("name", "?")), font=_font(26, medium=True), fill=TEXT
    )

    y = 84 + 110 + 16
    if effect:
        y += _draw_wrapped(d, pad, y, effect, f_body, TEXT, text_w, 27) + 12
    if formula:
        d.text((pad, y), "合成公式", font=_font(17, medium=True), fill=GOLD)
        y += 30
        fx = pad
        for e, ic in zip(formula, formula_icons):
            item = _load_icon(ic, 52)
            _paste_icon(img, item, fx, y, 52)
            f_n = _font(15)
            name = str(e.get("name", ""))[:6]
            tw = d.textlength(name, font=f_n)
            d.text((fx + (52 - tw) / 2, y + 56), name, font=f_n, fill=TEXT)
            fx += 78
        y += 84
    _footer(d, img, "数据来自腾讯官方资料库")
    return _finish(img, out)


# ---------------- trait card ----------------


def render_trait_card(trait_e: dict, icon: Path | None, season: str, out: Path) -> Path:
    pad = 24
    text_w = CARD_W - pad * 2
    f_body = _font(18)
    intro = str(trait_e.get("introduce", "")).replace("<br>", "\n")
    intro = intro.replace("<row>", "").replace("</row>", "")
    levels = trait_e.get("level", {})
    if isinstance(levels, str):
        levels = {}
    level_items = sorted(
        levels.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0
    )
    lv_height = sum(
        _wrapped_height(str(v), f_body, text_w - 70, 27) + 10 for _, v in level_items
    )
    height = (
        64 + 120 + 34 + _wrapped_height(intro, f_body, text_w, 27) + 20 + lv_height + 60
    )
    img, d = _new_canvas(CARD_W, height)
    _header(img, d, "云顶之弈 · 羁绊", season)

    icon_img = _load_icon(icon, 90)
    _paste_icon(img, icon_img, pad, 80, 90)
    d.text(
        (pad + 106, 92),
        str(trait_e.get("name", "?")),
        font=_font(26, medium=True),
        fill=TEXT,
    )

    y = 84 + 90 + 12
    if intro:
        y += _draw_wrapped(d, pad, y, intro, f_body, TEXT, text_w, 27) + 14
    for lv, desc in level_items:
        desc = str(desc).replace("<row>", "").replace("</row>", "")
        f_lv = _font(18, medium=True)
        d.rounded_rectangle([pad, y, pad + 52, y + 28], radius=4, fill=GOLD)
        tw = d.textlength(str(lv), font=f_lv)
        d.text((pad + (52 - tw) / 2, y + 3), str(lv), font=f_lv, fill=(15, 18, 24))
        d.text((pad + 70, y + 3), f"级效果（{lv} 个弈子）", font=_font(15), fill=DIM)
        y += 34
        y += _draw_wrapped(d, pad, y, desc, f_body, TEXT, text_w, 27) + 10
    _footer(d, img, "数据来自腾讯官方资料库")
    return _finish(img, out)
