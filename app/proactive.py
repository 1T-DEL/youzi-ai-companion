# -*- coding: utf-8 -*-
"""主动问候：后台线程，每隔一定间隔检查是否该开口，生成一条主动消息。
只在非免打扰时段触发，且基于已有记忆，不编造刚发生的事。
"""
import os
import threading
import time
from datetime import datetime

import liveliness


class Proactive:
    def __init__(self, cfg, llm, persona_builder, memory, on_generate, state_file=None):
        self.enabled = cfg.get("PROACTIVE_ENABLED") and str(cfg.get("PROACTIVE_ENABLED")).lower() in ("1", "true", "on", "yes")
        self.interval_min = int(cfg.get("PROACTIVE_INTERVAL_MINUTES") or 180)
        self.quiet_start = int(cfg.get("PROACTIVE_QUIET_START") or 23)
        self.quiet_end = int(cfg.get("PROACTIVE_QUIET_END") or 8)
        self.llm = llm
        self.persona_builder = persona_builder
        self.memory = memory
        self.on_generate = on_generate  # 回调，把主动消息分发出去（写库+外发）
        self._stop = threading.Event()
        self.state_file = state_file
        self._last = self._load_last() or time.time()  # 跨重启记住上次发送时间
        self._th = None

    def _load_last(self):
        try:
            if self.state_file and os.path.exists(self.state_file):
                import json
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return float(json.load(f).get("last") or 0)
        except Exception:
            pass
        return 0.0

    def _save_last(self):
        try:
            if self.state_file:
                import json
                d = os.path.dirname(self.state_file)
                if d:
                    os.makedirs(d, exist_ok=True)
                with open(self.state_file, "w", encoding="utf-8") as f:
                    json.dump({"last": self._last}, f)
        except Exception:
            pass

    def _in_quiet(self, now):
        h = now.hour
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= h < self.quiet_end
        # 跨天（如 23 -> 8）
        return h >= self.quiet_start or h < self.quiet_end

    def _due(self):
        return (time.time() - self._last) >= self.interval_min * 60

    def start(self):
        if not self.enabled:
            return
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            if self._due() and not self._in_quiet(datetime.now()):
                self.send_one_now()
                self._last = time.time()
                self._save_last()
            self._stop.wait(30)

    def send_one_now(self):
        """立即生成并分发一条主动消息，返回文本（失败返回 None）。"""
        if not self.llm.configured:
            return None
        try:
            facts = self.memory.all_facts()
            recent = self.memory.recent(6)
            system = self.persona_builder(facts=facts)
            context = "最近对话：\n" + "\n".join(
                (("对方：" if r == "user" else "你：") + c) for r, c in recent) if recent else "还没有对话记录。"
            user_prompt = (
                "现在是你主动找对方聊天的时候。结合你记得的关于对方的事和现在的时段，"
                "自然、生活化地说一句问候或关心（不要问句轰炸，像真人一样随口一句；"
                "多数时候用换行写成2~3条短消息连发，偶尔可以连发更多条，"
                "像打字很快很兴奋的样子；一句能说完就一行）。"
                "你可以带一点自己的小情绪：偶尔不完全顺着对方，撒个娇、小小地任性或想念一下，"
                "但要有分寸，别真怼人、别阴阳怪气。"
                "请只输出消息文本，不要加任何前缀或解释。\n\n" + context)
            reply = self.llm.chat([
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ], max_tokens=120)
            reply = reply.strip().strip('"')
            if reply:
                reply = liveliness.post_process(reply, "neu", kaomoji_prob=0.40)
                self.on_generate(reply)
                return reply
        except Exception:
            pass  # 主动消息失败不影响主流程
        return None

    def _send_one(self):
        self.send_one_now()
