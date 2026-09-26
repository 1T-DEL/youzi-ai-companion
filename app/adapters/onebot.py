# -*- coding: utf-8 -*-
"""OneBot 11（QQ 适配器）接入：后台线程连接 WebSocket，收发私聊/群消息。
handle_text(text, sender_id) 应返回回复文本；本适配器会把回复从同一 WS 发回。
支持：多行回复按行拆多条连发、QQ 表情收发（收到 face 转文字、回复随机带表情）、
    主动给最近联系人发消息（供主动问候模块调用）。
"""
import base64
import json
import os
import random
import re
import threading
import time

# QQ 表情 id -> 中文名（收消息时转文字用）
FACE_NAMES = {
    0: "惊讶", 1: "撇嘴", 2: "色", 3: "发呆", 4: "得意", 5: "流泪", 6: "害羞",
    7: "闭嘴", 8: "睡", 9: "大哭", 10: "尴尬", 11: "发怒", 12: "调皮", 13: "龇牙",
    14: "微笑", 15: "难过", 16: "酷", 17: "喷", 18: "抓狂", 19: "吐", 20: "偷笑",
    21: "可爱", 22: "白眼", 23: "傲慢", 24: "饥饿", 25: "困", 26: "惊恐", 27: "流汗",
    28: "憨笑", 29: "悠闲", 30: "奋斗", 31: "咒骂", 32: "疑问", 33: "嘘", 34: "晕",
    35: "疯了", 36: "衰", 37: "骷髅", 38: "敲打", 39: "再见", 40: "擦汗", 41: "抠鼻",
    42: "鼓掌", 43: "糗大了", 44: "坏笑", 45: "左哼哼", 46: "右哼哼", 47: "哈欠",
    48: "鄙视", 49: "委屈", 50: "快哭了", 51: "阴险", 52: "亲亲", 53: "吓", 54: "可怜",
}

# ---- 真贴纸表情包：文字后按"随机+冷却"补发一张表情（活人感） ----
STICKER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "stickers"))
GIF_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "gif_library"))
# 概率默认值；均可被 .env 覆盖（STICKER_PROB / STICKER_GIF_PROB / STICKER_COOLDOWN_MIN）
STICKER_PROB = 0.12        # 每条回复后跟一张表情的概率（降频：更随机）
STICKER_GIF_PROB = 0.70    # 命中时，优先发 GIF 动图表情的概率
STICKER_COOLDOWN_MIN = 15  # 同一会话两次表情之间的最短间隔（分钟）


def _load_stickers(exts=(".png",)):
    """返回贴纸文件路径列表；目录不存在时返回 []。"""
    try:
        if not os.path.isdir(STICKER_DIR):
            return []
        return [os.path.join(STICKER_DIR, f) for f in sorted(os.listdir(STICKER_DIR))
                if f.lower().endswith(exts)]
    except Exception:
        return []


def _load_gifs():
    try:
        if not os.path.isdir(GIF_DIR):
            return []
        return [os.path.join(GIF_DIR, f) for f in sorted(os.listdir(GIF_DIR))
                if f.lower().endswith(".gif")]
    except Exception:
        return []


class OneBot:
    def __init__(self, cfg, handle_text, log=None, state_file=None):
        cfg = cfg or {}
        self.cfg = cfg
        self.ws_url = cfg.get("ONEBOT_WS_URL") or ""
        self.token = cfg.get("ONEBOT_ACCESS_TOKEN") or ""
        self.enabled_flag = str(cfg.get("ONEBOT_ENABLED") or "").lower() in ("1", "true", "on", "yes")
        self.handle_text = handle_text  # callable(text, sender_id) -> reply text or None
        self.log = log or print
        self.state_file = state_file
        self.last_contact = self._load_last_contact()
        self._stop = threading.Event()
        self._th = None
        self._ws = None
        # 贴纸随机/冷却状态：全局发送时间戳队列 + 上次发送的文件名（避免连发同款）
        self._sticker_times = []
        self._last_sticker = ""
        self._sticker_lock = threading.Lock()

    def _load_last_contact(self):
        try:
            if self.state_file and os.path.exists(self.state_file):
                with open(self.state_file, "r", encoding="utf-8") as f:
                    d = json.load(f)
                return str(d.get("user_id") or "")
        except Exception:
            pass
        return ""

    def _save_last_contact(self):
        try:
            if self.state_file:
                with open(self.state_file, "w", encoding="utf-8") as f:
                    json.dump({"user_id": self.last_contact}, f, ensure_ascii=False)
        except Exception:
            pass

    def send_active(self, text, user_id):
        """主动给指定用户发一条私聊消息（简短问候，不带表情）。"""
        if not user_id or not self._ws:
            return
        segs = [{"type": "text", "data": {"text": text}}]
        try:
            self._ws.send(json.dumps({
                "action": "send_private_msg",
                "params": {"user_id": str(user_id), "message": segs},
                "echo": "active-" + str(int(time.time() * 1000)),
            }, ensure_ascii=False))
            self.log("[OneBot] 主动消息已发送到 " + str(user_id))
        except Exception as e:
            self.log("[OneBot] 主动消息发送失败：" + str(e))

    def enabled(self):
        return self.enabled_flag and bool(self.ws_url)

    def start(self):
        if not self.enabled():
            return
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        try:
            import websocket  # websocket-client
        except Exception:
            self.log("[OneBot] 未安装 websocket-client，跳过（pip install websocket-client）")
            return
        while not self._stop.is_set():
            try:
                self._run(websocket)
            except Exception as e:
                self.log("[OneBot] 连接异常：" + str(e))
            self._stop.wait(5)

    def _run(self, websocket):
        header = {
            "Authorization": "Bearer " + self.token,
            "X-OneBot-Token": self.token,
        } if self.token else {}
        ws = websocket.create_connection(self.ws_url, header=header, timeout=30)
        self._ws = ws
        self.log("[OneBot] 已连接 " + self.ws_url)
        while not self._stop.is_set():
            ws.settimeout(30)
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            self._dispatch(ev)
        ws.close()
        self._ws = None

    # ---- 处理事件 ----
    def _dispatch(self, ev):
        if ev.get("post_type") != "message":
            return
        msg_type = ev.get("message_type")
        sender_id = str(ev.get("user_id", ""))
        # 记住最近联系人（供主动问候）：只记私聊，对方发过东西就算
        if sender_id and msg_type == "private":
            self.last_contact = sender_id
            self._save_last_contact()
        text = ""
        image_segs = []
        video_segs = []
        face_ids = []
        for seg in ev.get("message", []):
            if seg.get("type") == "text":
                text += seg.get("data", {}).get("text", "")
            elif seg.get("type") == "image":
                image_segs.append(seg)
            elif seg.get("type") == "video":
                video_segs.append(seg)
            elif seg.get("type") == "face":
                fid = str(seg.get("data", {}).get("id", ""))
                if fid:
                    face_ids.append(fid)
        # 表情转文字：让柚子"看懂"对方发的 QQ 表情
        if face_ids:
            names = [FACE_NAMES.get(int(f), "表情") if f.isdigit() else "表情" for f in face_ids]
            text += " [对方发来QQ表情：{}]".format("、".join(names))
        # 图片识别：把图里内容转成文字前置，让柚子"看懂"图
        if image_segs:
            desc = self._describe_images(image_segs)
            if desc:
                text = ("[对方发来一张图片，图片内容：{}]\n{}".format(desc, text)).strip()
            else:
                text = ("[对方发来一张图片]\n{}".format(text)).strip()
        # 视频：本地视频文件抽帧识别画面；视频链接解析
        if video_segs:
            vdesc = self._describe_videos(video_segs)
            if vdesc:
                text = ("[对方发来一个视频，画面：{}]\n{}".format(vdesc, text)).strip()
            else:
                text = ("[对方发来一个视频]\n{}".format(text)).strip()
        text = text.strip()
        if not text:
            return
        try:
            self.log("[OneBot] 收到消息 {}：{}".format(msg_type, text[:50]))
        except Exception:
            pass
        reply = None
        try:
            reply = self.handle_text(text, sender_id)
        except Exception as e:
            self.log("[OneBot] 处理消息出错：" + str(e))
        if reply:
            self._send_reply(ev, reply)
            self._maybe_send_voice(ev, reply)

    def _describe_images(self, segs):
        import app.vision as vision
        key = self.cfg.get("DASHSCOPE_API_KEY") or ""
        model = self.cfg.get("VISION_MODEL") or "qwen-vl-max"
        parts = []
        for seg in segs[:1]:  # 一次最多识别 1 张，控制耗时
            d = vision.describe_from_segment(seg, key, model)
            if d:
                parts.append(d)
        return "；".join(parts) if parts else None

    def _voice_bytes(self, text):
        """把文字合成语音，返回 wav 字节；失败返回 None。"""
        try:
            from app.voice import Voice
            v = Voice(self.cfg, self.cfg.get("LLM_API_KEY") or "")
            return v.synthesize(text)
        except Exception as e:
            self.log("[OneBot] TTS 失败：" + str(e))
            return None

    def _maybe_send_voice(self, ev, reply):
        """按概率给回复补发一条语音消息（真人发语音条的感觉）。"""
        try:
            prob = float(self.cfg.get("VOICE_REPLY_PROB") or 0.25)
        except Exception:
            prob = 0.25
        if random.random() >= prob or not reply:
            self.log("[OneBot] 语音跳过(未命中概率或空回复)")
            return
        txt = str(reply).strip()
        if not txt:
            return
        audio = self._voice_bytes(txt[:200])
        if not audio:
            self.log("[OneBot] 语音：TTS 失败")
            return
        data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
        try:
            os.makedirs(data_dir, exist_ok=True)
        except Exception:
            pass
        path = os.path.join(data_dir, "voice_reply_%d.wav" % int(time.time() * 1000))
        try:
            with open(path, "wb") as f:
                f.write(audio)
        except Exception as e:
            self.log("[OneBot] 语音文件写入失败：" + str(e))
            return
        msg_type = ev.get("message_type")
        params = {"message": [{"type": "record", "data": {"file": path}}]}
        if msg_type == "group":
            params["group_id"] = ev.get("group_id")
            action = "send_group_msg"
        else:
            params["user_id"] = ev.get("user_id")
            action = "send_private_msg"
        if not self._ws:
            return
        try:
            self._ws.send(json.dumps({"action": action, "params": params,
                                      "echo": "voice-" + str(int(time.time() * 1000))}, ensure_ascii=False))
            self.log("[OneBot] 语音回复已发送：" + path)
        except Exception as e:
            self.log("[OneBot] 语音回复发送失败：" + str(e))

    def _describe_videos(self, segs):
        import app.video as video
        key = self.cfg.get("DASHSCOPE_API_KEY") or ""
        model = self.cfg.get("VISION_MODEL") or "qwen-vl-max"
        parts = []
        for seg in segs[:1]:  # 一次最多处理 1 个视频，控制耗时
            d = seg.get("data") or {}
            fp = d.get("file") or ""
            url = d.get("url") or ""
            if fp and fp.startswith("file://"):
                fp = fp[7:]
            if fp and os.path.exists(fp):
                desc = video.describe_video_file(fp, key, model, log=self.log)
                if desc:
                    parts.append(desc)
            elif url and (url.startswith("http://") or url.startswith("https://")):
                ck = (self.cfg.get("VIDEO_COOKIES_FILE") or "").strip() or None
                desc = video.describe_video_link(url, cookiefile=ck,
                                                 vision_key=key, vision_model=model)
                if desc:
                    parts.append(desc)
        return "；".join(parts) if parts else None

    def _send_reply(self, ev, reply):
        # 提取回复里显式带的 [face:N] 表情标记
        explicit_faces = []
        def _pick(m):
            try:
                explicit_faces.append(int(m.group(1)))
            except Exception:
                pass
            return ""
        clean = re.sub(r"\[face:(\d+)\]", _pick, str(reply))
        # 多行回复：按行拆成多条短消息连发（条数随机，不固定）
        lines = [ln.strip() for ln in clean.split("\n") if ln.strip()]
        if len(lines) > 1 and random.random() < 0.7:
            n = random.randint(2, min(3, len(lines)))  # 随机 2~3 条，避免总固定三条
            chunk = lines[:n]
        else:
            chunk = [lines[0] if lines else clean]
        for i, ln in enumerate(chunk):
            segs = [{"type": "text", "data": {"text": ln}}]
            if i == 0 and explicit_faces:
                for fid in explicit_faces:
                    segs.append({"type": "face", "data": {"id": fid}})
            self._send_one(ev, segs)
            if len(chunk) > 1:
                time.sleep(0.45)
        # 真贴纸：文字发完后，按"概率+冷却"单独补发一张表情（GIF动图优先，像真人发贴纸）
        if not explicit_faces and self._sticker_allowed():
            self._send_sticker(ev)

    # ---- 贴纸随机/冷却 ----
    def _sticker_prob(self):
        try:
            return float(self.cfg.get("STICKER_PROB") or STICKER_PROB)
        except Exception:
            return STICKER_PROB

    def _sticker_cooldown_min(self):
        try:
            return float(self.cfg.get("STICKER_COOLDOWN_MIN") or STICKER_COOLDOWN_MIN)
        except Exception:
            return STICKER_COOLDOWN_MIN

    def _sticker_allowed(self):
        """命中概率 + 冷却检查：同一会话间隔内最多发一次，避免一直刷。"""
        if random.random() >= self._sticker_prob():
            return False
        now = time.time()
        cool = self._sticker_cooldown_min() * 60
        with self._sticker_lock:
            self._sticker_times = [t for t in self._sticker_times if now - t < cool]
            if self._sticker_times:
                return False
            self._sticker_times.append(now)
        return True

    def _send_sticker(self, ev):
        """随机选一张表情发出去：优先 GIF 动图库，其次静态贴纸；同款不连发。"""
        try:
            gif_prob = float(self.cfg.get("STICKER_GIF_PROB") or STICKER_GIF_PROB)
        except Exception:
            gif_prob = STICKER_GIF_PROB
        gifs = _load_gifs()
        stickers = _load_stickers() if not gifs or random.random() >= gif_prob else []
        pool = (gifs if gifs and not stickers else stickers) or gifs or stickers
        if not pool:
            return
        candidates = [p for p in pool if p != self._last_sticker] or pool
        p = random.choice(candidates)
        try:
            with open(p, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            self._send_one(ev, [{"type": "image", "data": {"file": "base64://" + b64}}])
            self._last_sticker = p
            self.log("[OneBot] 已发送表情：{}（{}）".format(
                os.path.basename(p), "动图" if p.lower().endswith(".gif") else "贴纸"))
        except Exception as e:
            self.log("[OneBot] 表情发送失败：" + str(e))

    def _send_one(self, ev, segs):
        msg_type = ev.get("message_type")
        params = {"message": segs}
        if msg_type == "group":
            params["group_id"] = ev.get("group_id")
            params["message_type"] = "group"
            action = "send_group_msg"
        else:
            params["user_id"] = ev.get("user_id")
            action = "send_private_msg"
        if not self._ws:
            return
        try:
            self._ws.send(json.dumps({
                "action": action,
                "params": params,
                "echo": "reply-" + str(int(time.time() * 1000)),
            }, ensure_ascii=False))
        except Exception as e:
            self.log("[OneBot] 回发失败：" + str(e))
