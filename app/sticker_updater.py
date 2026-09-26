# -*- coding: utf-8 -*-
"""柚子贴纸自动更新：
1. 首次/启动时确保贴纸库达到目标数量；
2. 每 12 小时检查一次，从候选池里随机补充"新贴纸"（字牌贴纸 + 表情贴纸），
   名字去重、数量有上限，保持库常新——不用手动加图。
字牌贴纸：粉嫩圆角卡片 + 可爱文案（贴贴/抱抱/想你…）。
"""
import os
import random
import threading
import time

from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.abspath(__file__))
STICKER_DIR = os.path.abspath(os.path.join(BASE, "..", "data", "stickers"))
SIZE = 360
TARGET_MIN = 16      # 库少于这个数就补
TARGET_MAX = 30      # 库最多保留这么多
CHECK_HOURS = 12     # 每 12 小时检查一次

# 字牌贴纸文案池（随机挑，不会重复加）
TEXT_POOL = [
    "贴贴", "抱抱", "想你", "晚安", "早安", "在呢", "好耶", "嘿嘿",
    "亲亲", "摸摸头", "别怕", "我在", "加油", "委屈", "哼！", "开心",
    "么么", "嗯嗯", "呜呜", "嘿嘿嘿", "辛苦啦", "早点睡", "吃了吗", "陪你",
    "乖~", "蹭蹭", "等你好久了", "不许熬夜", "给你糖", "喵~", "冲呀",
]
# 表情贴纸补充池（emoji 字符）
EMOJI_POOL = [
    "🥰", "😘", "😍", "🤗", "😊", "😆", "🥳", "😎", "🤭", "😉",
    "🫶", "🫰", "💕", "💖", "💝", "🌷", "🌸", "🌈", "⭐", "🍬",
    "🍭", "🍓", "🧸", "🎉", "🔥", "💤", "🍂", "☕", "🧋", "🐣",
]


def _font_candidates():
    windir = os.environ.get("WINDIR", "C:\\Windows")
    return [
        os.path.join(windir, "Fonts", "simkai.ttf"),     # 楷体（最可爱）
        os.path.join(windir, "Fonts", "msyhbd.ttc"),     # 微软雅黑粗
        os.path.join(windir, "Fonts", "simhei.ttf"),     # 黑体兜底
    ]


def _load_font(size):
    for p in _font_candidates():
        try:
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _exists(name):
    if not os.path.isdir(STICKER_DIR):
        return False
    return os.path.exists(os.path.join(STICKER_DIR, name + ".png"))


def _save(img, name):
    os.makedirs(STICKER_DIR, exist_ok=True)
    img.save(os.path.join(STICKER_DIR, name + ".png"), "PNG")
    return name


def make_text_sticker(word):
    """字牌贴纸：随机粉嫩圆角卡片 + 居中文字"""
    pastels = [(255, 214, 222), (255, 235, 205), (214, 236, 255),
               (222, 255, 222), (238, 222, 255), (255, 245, 214)]
    bg = random.choice(pastels)
    edge = tuple(max(0, c - 90) for c in bg)  # 深一档的描边色
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 圆角卡片
    d.rounded_rectangle([22, 22, SIZE - 22, SIZE - 22], radius=64, fill=bg + (255,), outline=edge + (255,), width=6)
    # 小装饰：两颗小心
    d.ellipse([70, 78, 98, 106], fill=(255, 130, 150, 255))
    d.ellipse([98, 88, 126, 116], fill=(255, 160, 175, 255))
    d.ellipse([SIZE - 126, SIZE - 116, SIZE - 98, SIZE - 88], fill=(255, 160, 175, 255))
    d.ellipse([SIZE - 98, SIZE - 106, SIZE - 70, SIZE - 78], fill=(255, 130, 150, 255))
    font = _load_font(118 if len(word) <= 3 else 92)
    try:
        bbox = d.textbbox((0, 0), word, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (SIZE - w) / 2 - bbox[0]
        y = (SIZE - h) / 2 - bbox[1] + 6
        d.text((x, y), word, font=font, fill=(90, 60, 80, 255))
    except Exception:
        pass
    return _save(img, "txt_" + word)


def make_emoji_sticker(ch):
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font = _load_font(250)
    try:
        bbox = d.textbbox((0, 0), ch, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (SIZE - w) / 2 - bbox[0]
        y = (SIZE - h) / 2 - bbox[1]
        d.text((x, y), ch, font=font, embedded_color=True)
    except Exception:
        pass
    return _save(img, "em_" + str(abs(hash(ch))))


def current_pack():
    if not os.path.isdir(STICKER_DIR):
        return []
    return [f for f in os.listdir(STICKER_DIR) if f.lower().endswith(".png")]


def ensure_pack(force_add=0):
    """保证贴纸库数量在 [TARGET_MIN, TARGET_MAX]，必要时补充新贴纸。
    force_add>0 时强制再新增若干张（自动更新/手动刷新用）。
    返回本次新增的贴纸名列表。"""
    added = []
    try:
        pack = current_pack()
        cur = len(pack)
        need = max(TARGET_MIN - cur, force_add)
        if need <= 0:
            return added
        # 已用的字词/表情（防重）
        used_txt = {f[4:-4] for f in pack if f.startswith("txt_")}
        used_em = {f for f in pack if f.startswith("em_")}
        avail_txt = [w for w in TEXT_POOL if w not in used_txt]
        avail_em = [e for e in EMOJI_POOL if e not in used_em]
        random.shuffle(avail_txt)
        random.shuffle(avail_em)
        itxt, iem = iter(avail_txt), iter(avail_em)
        while len(added) < need and cur + len(added) < TARGET_MAX:
            made = False
            try:
                w = next(itxt)
                make_text_sticker(w)
                added.append("txt_" + w)
                made = True
            except StopIteration:
                pass
            if not made:
                try:
                    e = next(iem)
                    make_emoji_sticker(e)
                    added.append("em_" + e)
                    made = True
                except StopIteration:
                    break
        return added
    except Exception:
        return added


def _loop():
    while True:
        try:
            added = ensure_pack(force_add=1)  # 每轮强制补 1 张新的，保持常新
            if added:
                print("[贴纸更新] 新增贴纸：%s" % "、".join(added))
        except Exception:
            pass
        time.sleep(CHECK_HOURS * 3600)


def start_auto_update():
    """服务启动时调用：先确保基础库，再后台每 12 小时补新贴纸。"""
    added = ensure_pack(force_add=0)
    if added:
        print("[贴纸更新] 初始补充：%s" % "、".join(added))
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


if __name__ == "__main__":
    added = ensure_pack(force_add=int(os.environ.get("FORCE_ADD", "0")) or 0)
    print("当前贴纸数:", len(current_pack()))
    print("本次新增:", added if added else "无")
