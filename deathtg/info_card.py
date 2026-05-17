from __future__ import annotations

import math
import subprocess
import time
from pathlib import Path

import psutil
from PIL import Image, ImageDraw, ImageFont

from deathtg.assets import default_avatar_path
from deathtg.config import ROOT_DIR, RUNTIME_DIR


CARD_W = 1280
CARD_H = 780
PANEL_AVATAR = default_avatar_path() or (ROOT_DIR / "deathtg" / "panel" / "static" / "default_avatar.png")
PROCESS_STARTED_AT = time.time()


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates.extend(
            [
                "DejaVuSans-Bold.ttf",
                r"C:\Windows\Fonts\segoeuib.ttf",
                r"C:\Windows\Fonts\arialbd.ttf",
            ]
        )
    candidates.extend(
        [
            "DejaVuSans.ttf",
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\arial.ttf",
        ]
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill, outline=None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, *, size: int, bold: bool = False):
    current = size
    while current >= 18:
        font = _font(current, bold=bold)
        width = draw.textbbox((0, 0), text, font=font)[2]
        if width <= max_width:
            return font
        current -= 2
    return _font(18, bold=bold)


def _draw_wrapped(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    max_width: int,
    *,
    font,
    fill,
    line_gap: int = 8,
    max_lines: int = 3,
) -> None:
    words = str(text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if lines and len(" ".join(lines)) < len(str(text)):
        while lines[-1] and draw.textbbox((0, 0), lines[-1] + "...", font=font)[2] > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip() + "..."
    x, y = xy
    line_h = draw.textbbox((0, 0), "Ag", font=font)[3] + line_gap
    for index, line in enumerate(lines):
        draw.text((x, y + index * line_h), line, fill=fill, font=font)


def _crop_avatar(path: Path, size: int) -> Image.Image:
    if not path.exists():
        return Image.new("RGB", (size, size), (38, 20, 62))
    image = Image.open(path).convert("RGB")
    side = min(image.size)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side)).resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size, size), radius=42, fill=255)
    out = Image.new("RGBA", (size, size))
    out.paste(image, (0, 0))
    out.putalpha(mask)
    return out


def _draw_usage_chart(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    usage_points: list[dict],
    *,
    line_color: tuple[int, int, int] = (88, 233, 255),
) -> None:
    left, top, right, bottom = rect
    _rounded(draw, rect, 22, fill=(32, 14, 52), outline=(*line_color, 60))
    points = usage_points[-30:] if usage_points else []
    values = [int(item.get("count", 0)) for item in points]
    labels = [str(item.get("day", ""))[5:] for item in points]
    if not values or max(values) == 0:
        draw.text((left + 22, top + 18), "No activity yet", fill=(190, 177, 212), font=_font(24, bold=True))
        draw.text((left + 22, top + 52), "Use commands to build usage stats.", fill=(169, 153, 196), font=_font(18))
        return
    max_value = max(1, max(values))
    chart_left = left + 30
    chart_top = top + 42
    chart_right = right - 24
    chart_bottom = bottom - 42
    width = max(1, chart_right - chart_left)
    height = max(1, chart_bottom - chart_top)
    non_zero = [idx for idx, value in enumerate(values) if value > 0]
    sparse = len(non_zero) <= 2

    grid_rows = 3 if sparse else 5
    for idx in range(grid_rows):
        y = chart_top + idx * height / max(1, (grid_rows - 1))
        draw.line((chart_left, y, chart_right, y), fill=(255, 255, 255, 16), width=1)

    points = []
    count = max(1, len(values) - 1)
    for idx, value in enumerate(values):
        x = chart_left + idx * width / count
        y = chart_bottom - (value / max_value) * height
        points.append((x, y))

    for idx in range(len(points) - 1):
        draw.line((*points[idx], *points[idx + 1]), fill=(*line_color, 170 if sparse else 255), width=3 if sparse else 4)

    for idx, point in enumerate(points):
        if values[idx] > 0 or idx in {0, len(points) - 1}:
            radius = 7 if values[idx] > 0 else 4
            draw.ellipse(
                (point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius),
                fill=(255, 255, 255),
                outline=line_color,
                width=2,
            )
        if idx % 6 == 0 or idx == len(points) - 1:
            draw.text((point[0] - 16, chart_bottom + 10), labels[idx], fill=(190, 177, 212), font=_font(16))
    if sparse and non_zero:
        peak_idx = max(non_zero, key=lambda i: values[i])
        peak = points[peak_idx]
        label = f"{values[peak_idx]}"
        label_font = _font(16, bold=True)
        label_w = draw.textbbox((0, 0), label, font=label_font)[2]
        label_h = draw.textbbox((0, 0), label, font=label_font)[3]
        label_x = min(max(left + 8, peak[0] + 10), right - label_w - 8)
        label_y = min(max(top + 8, peak[1] - 18), bottom - label_h - 8)
        draw.text((label_x, label_y), label, fill=(241, 238, 249), font=label_font)


def _git_version() -> tuple[str, str]:
    try:
        version = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT_DIR, text=True, timeout=4).strip()
    except Exception:
        version = "local"
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT_DIR, text=True, timeout=4).strip()
    except Exception:
        branch = "main"
    return version, branch


def _system_metrics() -> tuple[str, str, str]:
    try:
        cpu = f"{psutil.cpu_percent(interval=0.05):.0f}%"
    except Exception:
        cpu = "n/a"
    try:
        memory = psutil.virtual_memory()
        ram = f"{memory.percent:.0f}%"
    except Exception:
        ram = "n/a"
    uptime_seconds = max(1, int(time.time() - psutil.boot_time()))
    days, rem = divmod(uptime_seconds, 86400)
    hours = rem // 3600
    uptime = f"{days}d {hours}h" if days else f"{hours}h {(rem % 3600) // 60}m"
    return cpu, ram, uptime


def render_info_card(
    *,
    title: str,
    username: str,
    description: str,
    prefix: str,
    uses: int,
    days: int,
    level: int,
    level_current: int,
    top_modules_text: str,
    usage_points: list[dict],
    accent: str = "blue",
) -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    version, branch = _git_version()
    cpu, ram, system_uptime = _system_metrics()
    card_path = RUNTIME_DIR / "info_card.png"

    accent = (accent or "blue").lower()
    palette = {
        "blue": {"bg1": (22, 8, 36), "bg2": (50, 25, 76), "line": (88, 233, 255), "gold": (235, 196, 110)},
        "red": {"bg1": (36, 8, 18), "bg2": (84, 25, 36), "line": (255, 110, 150), "gold": (255, 184, 120)},
        "gold": {"bg1": (34, 20, 8), "bg2": (90, 56, 22), "line": (255, 209, 102), "gold": (255, 224, 140)},
        "green": {"bg1": (8, 28, 18), "bg2": (18, 76, 42), "line": (82, 255, 139), "gold": (238, 255, 204)},
        "purple": {"bg1": (22, 10, 42), "bg2": (66, 28, 102), "line": (186, 134, 255), "gold": (236, 205, 255)},
        "dark": {"bg1": (10, 12, 18), "bg2": (22, 28, 38), "line": (160, 176, 196), "gold": (210, 220, 232)},
    }.get(accent, {"bg1": (22, 8, 36), "bg2": (50, 25, 76), "line": (88, 233, 255), "gold": (235, 196, 110)})

    image = Image.new("RGB", (CARD_W, CARD_H), palette["bg1"])
    draw = ImageDraw.Draw(image)
    for y in range(CARD_H):
        blend = y / CARD_H
        color = (
            int(palette["bg1"][0] + (palette["bg2"][0] - palette["bg1"][0]) * blend),
            int(palette["bg1"][1] + (palette["bg2"][1] - palette["bg1"][1]) * blend),
            int(palette["bg1"][2] + (palette["bg2"][2] - palette["bg1"][2]) * blend),
        )
        draw.line((0, y, CARD_W, y), fill=color)

    _rounded(draw, (36, 36, CARD_W - 36, CARD_H - 36), 34, fill=(41, 15, 66), outline=(132, 90, 188), width=2)
    _rounded(draw, (64, 64, CARD_W - 64, 360), 28, fill=(52, 25, 84), outline=palette["line"], width=2)

    avatar = _crop_avatar(PANEL_AVATAR, 170)
    image.paste(avatar, (92, 108), avatar)

    draw.text((290, 98), "PROFILE", fill=palette["gold"], font=_font(24, bold=True))
    title_font = _fit_text(draw, title, 820, size=68, bold=True)
    draw.text((290, 132), title, fill=(240, 242, 249), font=title_font)
    draw.text((290, 228), username, fill=(186, 155, 220), font=_font(28))
    desc_font = _fit_text(draw, description, 820, size=26)
    _draw_wrapped(draw, description, (290, 282), 820, font=desc_font, fill=(236, 226, 255), line_gap=6, max_lines=2)

    chip_font = _font(24, bold=True)
    chips = [
        ("Version", f"2.0.0 #{version}"),
        ("Branch", branch),
        ("Prefix", prefix),
        ("CPU", cpu),
        ("RAM", ram),
        ("VDS uptime", system_uptime),
        ("Actions", str(uses)),
        ("Level", f"{level}  {level_current}/100"),
    ]
    chip_x = 92
    chip_y = 392
    for idx, (label, value) in enumerate(chips):
        row = idx // 4
        col = idx % 4
        x = chip_x + col * 274
        y = chip_y + row * 98
        _rounded(draw, (x, y, x + 252, y + 78), 22, fill=(45, 21, 72), outline=(110, 90, 168))
        draw.text((x + 18, y + 16), label, fill=palette["line"], font=_font(20, bold=True))
        draw.text((x + 18, y + 42), value, fill=(243, 239, 251), font=chip_font)

    _draw_usage_chart(draw, (92, 598, 770, 730), usage_points, line_color=palette["line"])
    _rounded(draw, (794, 598, 1188, 730), 22, fill=(32, 14, 52), outline=(88, 233, 255, 60))
    draw.text((820, 620), "Top modules", fill=palette["line"], font=_font(24, bold=True))
    _draw_wrapped(
        draw,
        top_modules_text or "No stats yet",
        (820, 660),
        330,
        font=_font(24),
        fill=(241, 238, 249),
        max_lines=2,
    )

    uptime_seconds = max(1, int(time.time() - PROCESS_STARTED_AT))
    uptime_text = f"{uptime_seconds // 3600}:{(uptime_seconds % 3600) // 60:02d}:{uptime_seconds % 60:02d}"
    draw.text((CARD_W - 290, 82), f"Uptime {uptime_text}", fill=(198, 185, 228), font=_font(22))

    image.save(card_path)
    return card_path
