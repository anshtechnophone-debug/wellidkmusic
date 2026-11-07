from __future__ import annotations

from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import math
import random


def _font(size: int, bold: bool = False):
    paths = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"]
        if bold else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"]
    )
    for path in paths:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit(draw, text, max_width, start=58, minimum=25, bold=True):
    for size in range(start, minimum - 1, -2):
        font = _font(size, bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
    return _font(minimum, bold)


def _clean(value, fallback):
    return " ".join(str(value or fallback).split())


def make_player_cover(title: str, artist: str, requested_by: str, bot_username: str = "") -> BytesIO:
    """Create a premium XYRON Music visual. Song artwork is deliberately not reused."""
    W, H = 1280, 720
    random.seed(27)

    # Deep midnight gradient with violet and electric-blue ambient lighting.
    base = Image.new("RGB", (W, H))
    pix = base.load()
    for y in range(H):
        for x in range(W):
            blue = max(0.0, 1 - math.sqrt(((x - 1040) / 700) ** 2 + ((y - 150) / 570) ** 2))
            violet = max(0.0, 1 - math.sqrt(((x - 170) / 680) ** 2 + ((y - 660) / 520) ** 2))
            pix[x, y] = (
                min(255, int(5 + 35 * violet + 13 * blue)),
                min(255, int(8 + 17 * violet + 28 * blue)),
                min(255, int(22 + 52 * violet + 78 * blue)),
            )

    # Large soft neon blooms.
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((760, -100, 1390, 530), fill=(42, 123, 255, 110))
    gd.ellipse((-180, 360, 480, 1020), fill=(146, 57, 255, 78))
    glow = glow.filter(ImageFilter.GaussianBlur(105))
    canvas = Image.alpha_composite(base.convert("RGBA"), glow)
    d = ImageDraw.Draw(canvas)

    # Fine stars / dust for depth.
    for _ in range(115):
        x, y = random.randint(35, W - 35), random.randint(30, H - 30)
        r = random.choice((1, 1, 2, 3))
        alpha = random.randint(25, 100)
        d.ellipse((x-r, y-r, x+r, y+r), fill=(190, 218, 255, alpha))

    # Elegant outer frame and translucent left content panel.
    d.rounded_rectangle((34, 34, W - 34, H - 34), radius=42,
                        outline=(153, 190, 255, 105), width=2)
    d.rounded_rectangle((68, 74, 790, 646), radius=34,
                        fill=(8, 14, 32, 168), outline=(155, 185, 255, 48), width=2)

    # Oversized vinyl record on the right, with concentric grooves and neon rim.
    cx, cy = 1005, 350
    d.ellipse((790, 110, 1220, 540), fill=(7, 10, 24, 245),
              outline=(110, 166, 255, 235), width=5)
    for radius, color, width in [
        (198, (64, 91, 147, 125), 2), (180, (55, 82, 132, 110), 2),
        (162, (80, 105, 158, 100), 2), (142, (53, 75, 125, 110), 2),
        (120, (75, 99, 151, 90), 2),
    ]:
        d.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), outline=color, width=width)
    d.ellipse((cx-72, cy-72, cx+72, cy+72), fill=(31, 52, 98, 255),
              outline=(135, 191, 255, 255), width=4)
    # XYRON sound-wave emblem in the record label.
    for i, h in enumerate((24, 43, 63, 84, 63, 43, 24)):
        x = cx - 48 + i * 14
        d.rounded_rectangle((x, cy-h//2, x+7, cy+h//2), radius=4,
                            fill=(140, 202, 255, 255))
    d.ellipse((cx-7, cy-7, cx+7, cy+7), fill=(245, 249, 255, 255))

    # Small top brand signature.
    d.text((104, 105), "XYRON", font=_font(48, True), fill=(248, 250, 255, 255))
    d.text((108, 160), "M U S I C", font=_font(19, True), fill=(143, 190, 255, 255))
    d.rounded_rectangle((106, 216, 315, 260), radius=20,
                        fill=(35, 95, 176, 220), outline=(120, 185, 255, 100), width=1)
    d.ellipse((126, 231, 140, 245), fill=(86, 245, 177, 255))
    d.text((153, 225), "NOW PLAYING", font=_font(17, True), fill=(240, 248, 255, 255))

    title = _clean(title, "Unknown track")
    artist = _clean(artist, "Unknown artist")
    requester = _clean(requested_by, "Telegram user")
    title_font = _fit(d, title, 625, start=55, minimum=30, bold=True)
    artist_font = _fit(d, artist, 620, start=35, minimum=23, bold=False)
    d.text((106, 312), title, font=title_font, fill=(255, 255, 255, 255))
    d.rounded_rectangle((108, 390, 170, 396), radius=3, fill=(117, 179, 255, 255))
    d.text((106, 420), artist, font=artist_font, fill=(175, 207, 255, 255))

    # Requester identity chip.
    d.rounded_rectangle((104, 520, 735, 594), radius=24,
                        fill=(20, 35, 66, 238), outline=(105, 159, 235, 100), width=2)
    d.ellipse((124, 536, 166, 578), fill=(52, 103, 185, 255))
    d.text((137, 542), "♪", font=_font(25, True), fill=(255, 255, 255, 255))
    d.text((184, 532), "REQUESTED BY", font=_font(14, True), fill=(133, 178, 242, 255))
    requester_font = _fit(d, requester, 500, start=25, minimum=18, bold=True)
    d.text((184, 553), requester, font=requester_font, fill=(250, 252, 255, 255))

    # Equalizer strip and bot signature.
    heights = [18, 29, 46, 32, 58, 38, 72, 42, 62, 30, 48, 24, 38, 18]
    start_x = 835
    for i, h in enumerate(heights):
        x = start_x + i * 24
        d.rounded_rectangle((x, 600-h, x+10, 600+h//3), radius=5,
                            fill=(115, 183, 255, 190 if i % 2 else 245))
    handle = ("@" + bot_username.lstrip("@")) if bot_username else "XYRON MUSIC"
    d.text((850, 620), handle[:30], font=_font(17, True), fill=(174, 205, 255, 235))

    out = BytesIO()
    out.name = "xyron_music_player.png"
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    out.seek(0)
    return out
