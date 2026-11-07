from __future__ import annotations

from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import math
import textwrap


def _font(size: int, bold: bool = False):
    candidates = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"]
        if bold else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"]
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit(draw, text, max_width, start=54, minimum=28, bold=True):
    for size in range(start, minimum - 1, -2):
        font = _font(size, bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
    return _font(minimum, bold)


def make_player_cover(title: str, artist: str, requested_by: str, bot_username: str = "") -> BytesIO:
    """Create a premium branded XYRON Music cover; no third-party song artwork is used."""
    width, height = 1280, 720
    base = Image.new("RGB", (width, height), (8, 13, 28))
    px = base.load()
    for y in range(height):
        for x in range(width):
            glow = max(0.0, 1.0 - math.sqrt(((x - 1000) / 850) ** 2 + ((y - 70) / 650) ** 2))
            left = max(0.0, 1.0 - math.sqrt(((x - 80) / 700) ** 2 + ((y - 700) / 550) ** 2))
            px[x, y] = (
                int(8 + 18 * glow + 10 * left),
                int(13 + 28 * glow + 8 * left),
                int(28 + 58 * glow + 24 * left),
            )

    # Soft ambient glow behind the XYRON emblem.
    glow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    gd.ellipse((850, 75, 1210, 435), fill=(60, 145, 255, 90))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(85))
    base = Image.alpha_composite(base.convert("RGBA"), glow_layer)
    draw = ImageDraw.Draw(base)

    # Subtle equalizer lines.
    for i in range(54):
        x = 770 + i * 8
        bar = 20 + ((i * 37) % 110)
        draw.rounded_rectangle((x, 610 - bar, x + 4, 610), radius=2,
                               fill=(91, 164, 255, 95))

    # Framed brand panel.
    draw.rounded_rectangle((54, 48, 1226, 672), radius=42,
                           fill=(12, 21, 42, 238), outline=(91, 160, 255, 170), width=3)
    draw.rounded_rectangle((86, 82, 1194, 638), radius=30,
                           outline=(255, 255, 255, 25), width=2)

    # Brand mark: custom sound-wave monogram.
    draw.ellipse((930, 128, 1140, 338), fill=(25, 54, 105, 230),
                 outline=(107, 179, 255, 255), width=5)
    center_x, center_y = 1035, 233
    for i, h in enumerate([42, 75, 112, 145, 112, 75, 42]):
        x = center_x - 72 + i * 24
        draw.rounded_rectangle((x, center_y - h // 2, x + 12, center_y + h // 2),
                               radius=6, fill=(118, 194, 255, 255))
    draw.text((88, 112), "XYRON", font=_font(54, True), fill=(245, 249, 255, 255))
    draw.text((91, 174), "M U S I C", font=_font(22, True), fill=(123, 185, 255, 255))
    draw.rounded_rectangle((88, 240, 340, 286), radius=22, fill=(22, 93, 156, 210))
    draw.text((111, 250), "NOW PLAYING", font=_font(20, True), fill=(226, 242, 255, 255))

    title = " ".join(str(title or "Unknown track").split())
    artist = " ".join(str(artist or "Unknown artist").split())
    requested_by = " ".join(str(requested_by or "Telegram user").split())
    title_font = _fit(draw, title, 790, start=52, minimum=30, bold=True)
    artist_font = _fit(draw, artist, 780, start=34, minimum=24, bold=False)
    draw.text((90, 350), title, font=title_font, fill=(255, 255, 255, 255))
    draw.text((92, 430), artist, font=artist_font, fill=(160, 202, 255, 255))

    draw.rounded_rectangle((88, 520, 820, 590), radius=24,
                           fill=(20, 38, 67, 245), outline=(78, 139, 220, 150), width=2)
    draw.text((116, 536), "REQUESTED BY", font=_font(16, True), fill=(126, 177, 238, 255))
    req_font = _fit(draw, requested_by, 490, start=27, minimum=19, bold=True)
    draw.text((330, 532), requested_by, font=req_font, fill=(245, 248, 255, 255))

    username = ("@" + bot_username.lstrip("@")) if bot_username else "@XYRON"
    draw.text((90, 610), username[:36], font=_font(20, True), fill=(126, 178, 239, 255))
    draw.text((930, 555), "STREAM • CONNECT • ENJOY", font=_font(16, True),
              fill=(150, 194, 255, 230))

    output = BytesIO()
    output.name = "xyron_music_player.png"
    base.convert("RGB").save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
