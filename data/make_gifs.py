# -*- coding: utf-8 -*-
"""生成 GIF 动图表情库（data/gif_library/*.gif）
用 Segoe UI Emoji 渲染 + 逐帧动画：心跳/眨眼/摇摆/闪烁/弹跳。
PIL 逐帧保存为透明背景循环 GIF。手动替换/新增：把 .gif 放进 data/gif_library 即可。
"""
import os
import math

from PIL import Image, ImageDraw, ImageFont

SIZE = 320
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "gif_library"))
FONT_PATH = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "seguiemj.ttf")


def _font(size):
    try:
        if os.path.exists(FONT_PATH):
            return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        pass
    return ImageFont.load_default()


def _emoji_frame(ch, size, offset=(0, 0), alpha=255):
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = _font(size)
    try:
        bbox = d.textbbox((0, 0), ch, font=f)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (SIZE - w) / 2 - bbox[0] + offset[0]
        y = (SIZE - h) / 2 - bbox[1] + offset[1]
        d.text((x, y), ch, font=f, embedded_color=True)
    except Exception:
        pass
    if alpha < 255:
        a = img.split()[3].point(lambda v: int(v * alpha / 255))
        img.putalpha(a)
    return img


def _save(frames, name):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=120, loop=0, transparency=0, disposal=2)
    print("已生成:", path)
    return path


def heart_beat():
    frames = []
    for i in range(8):
        s = int(200 + 26 * math.sin(i * math.pi / 4))
        frames.append(_emoji_frame("💗", s))
    return _save(frames, "heart_beat.gif")


def blink_face():
    """眨眼：前几帧正常，中间一帧垂直压扁再恢复"""
    frames = []
    for i in range(8):
        if i == 3 or i == 4:
            img = _emoji_frame("😉", 230)
            img = img.crop((0, 60, SIZE, SIZE - 60)).resize((SIZE, SIZE - 120))
            base = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
            base.paste(img, (0, 60))
            frames.append(base)
        else:
            frames.append(_emoji_frame("😉", 230))
    return _save(frames, "blink_face.gif")


def sway_flower():
    frames = []
    for i in range(8):
        x = int(40 * math.sin(i * math.pi / 4))
        frames.append(_emoji_frame("🌷", 210, offset=(x, 0)))
    return _save(frames, "sway_flower.gif")


def star_sparkle():
    frames = []
    for i in range(8):
        a = int(120 + 135 * abs(math.sin(i * math.pi / 4)))
        frames.append(_emoji_frame("⭐", 240, alpha=a))
    return _save(frames, "star_sparkle.gif")


def bounce_heart():
    frames = []
    for i in range(8):
        y = int(50 * abs(math.sin(i * math.pi / 4)))
        frames.append(_emoji_frame("💖", 210, offset=(0, y)))
    return _save(frames, "bounce_heart.gif")


if __name__ == "__main__":
    heart_beat()
    blink_face()
    sway_flower()
    star_sparkle()
    bounce_heart()
    print("GIF 库共", len([f for f in os.listdir(OUT) if f.endswith(".gif")]), "张动图")
