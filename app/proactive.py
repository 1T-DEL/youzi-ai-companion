# -*- coding: utf-8 -*-
"""主动问候：后台线程，每隔一定间隔检查是否该开口，生成一条主动消息。
只在非免打扰时段触发，且基于已有记忆，不编造刚发生的事。
"""
import os
import random
import threading
import time
from datetime import datetime

import liveliness


# ---- 心情状态机：模拟她的情绪波动（不依赖大模型，轻量随机漂移） ----
# 心情决定主动频率与内容倾向：想你/兴奋更主动，低落会想被哄，平静就日常一点。
_MOODS = ["平静", "开心", "想你", "无聊", "小低落", "兴奋"]
_MOOD_BONUS = {"想你": 0.5, "兴奋": 0.45, "开心": 0.25, "平静": 0.0, "无聊": 0.1, "小低落": 0.08}
_MOOD_BIAS = {
    "想你": "这条主动消息可以带一点想念/想知道对方在干嘛的意味",
    "兴奋": "这条主动消息可以带点小兴奋，分享你此刻的小快乐或想拉对方一起做什么",
    "开心": "这条主动消息轻松愉快一点，自然分享近况或问候",
    "无聊": "这条主动消息带点无聊犯懒的小情绪，想找人聊两句",
    "小低落": "这条主动消息带一点点低落的尾巴（别太沉），半撒娇地想让对方理理你",
    "平静": "这条主动消息自然平常，像随口一句",
}

# 主动消息类型池：让"主动"不再是千篇一律的问候
MSG_TYPES = [
    ("问候", "自然地问候一句，贴合现在的时段和你们最近聊过的事，像真人随口一句"),
    ("联想", "由你记得的关于对方的某件事、或上次聊到的话题自然地提起，像突然想到一样"),
    ("近况", "说说你自己此刻在做什么、刚才经历的一件小事（你的日常切片），自然地分享，不用对方接也能继续说"),
    ("想你", "自然地表达一点想念、想知道对方在干嘛，别太黏、别轰炸"),
    ("小吐槽", "带点小脾气/撒娇地吐槽一句（今天遇到的小事、对方半天没理你之类），有分寸、不真生气"),
    ("提问", "随口问对方一个轻松的小问题（吃了吗/在忙什么/周末打算干什么），一问就够，别连环问"),
]


class Proactive:
    def __init__(self, cfg, llm, persona_builder, memory, on_generate, state_file=None, lifesim=None):
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
        # ---- 心情状态机 ----
        self._mood = random.choice(_MOODS)
        self._mood_next = time.time() + random.uniform(20, 45) * 60  # 心情每20~45分钟漂移一次
        # ---- 沉默续话：记录对方最后一次来消息的时间 ----
        self._last_user_msg = time.time()
        self.lifesim = lifesim  # 数字生命模拟器（可选）：心情与它统一

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

    def note_user_message(self):
        """对方来消息时调用：重置沉默计时。"""
        self._last_user_msg = time.time()

    def _drift_mood(self):
        """心情随机漂移：保持连续性（小概率大变，大概率小幅波动）。"""
        now = time.time()
        if now < self._mood_next:
            return
        self._mood_next = now + random.uniform(20, 45) * 60
        # 60% 小幅漂移（相邻心情），40% 全池随机
        if random.random() < 0.6:
            idx = _MOODS.index(self._mood)
            nxt = _MOODS[max(0, min(len(_MOODS) - 1, idx + random.choice([-1, 0, 1])))]
            self._mood = nxt
        else:
            self._mood = random.choice(_MOODS)

    def _effective_interval(self):
        """间隔自适应：好感度越高越主动；心情也会拉近或拉远。"""
        try:
            _a = self.memory.get_affection() or {}
            aff = _a.get("score") or 50
        except Exception:
            aff = 50
        base = max(20, self.interval_min * (1 - min(max(aff, 0), 100) / 100 * 0.35))
        base *= (1 - _MOOD_BONUS.get(self._mood, 0) * 0.5)   # 想你/兴奋时更频繁
        return base

    def _due(self):
        return (time.time() - self._last) >= self._effective_interval() * 60

    def _loop(self):
        while not self._stop.is_set():
            self._drift_mood()
            now = datetime.now()
            if self._due() and not self._in_quiet(now):
                self.send_one_now()
                self._last = time.time()
                self._save_last()
            # 沉默续话：对方 5 小时没说话，主动找补一句（避免"你不说话就断"）
            elif not self._in_quiet(now) and (time.time() - self._last_user_msg) > 5 * 3600:
                self.send_one_now(kind="续话")
                self._last_user_msg = time.time()
            self._stop.wait(60)

    def send_one_now(self, kind=None):
        """立即生成并分发一条主动消息，返回文本（失败返回 None）。

        kind：None=随机类型池（优先"由记得的事想起你"）；"续话"=对方沉默多时后的主动找补。
        小凌式：主动联系的原因来自"她想或她需要"——心情（lifesim 统一）+
        记得的对方关键事件（生日/纪念日/最近在忙的事）优先驱动，而不是固定模板问候。
        """
        if not self.llm.configured:
            return None
        try:
            facts = self.memory.all_facts()
            recent = self.memory.recent(6)
            system = self.persona_builder(facts=facts)
            context = "最近对话：\n" + "\n".join(
                (("对方：" if r == "user" else "你：") + c) for r, c in recent) if recent else "还没有对话记录。"
            # 统一心情：以数字生命模拟器的状态为准（她的生活独立运转）
            mood = self.lifesim.mood() if self.lifesim else self._mood
            if kind == "续话":
                tname, tdesc = "续话", ("对方已经好一阵子没说话了，自然地说一句不显得刻意的话"
                                        "（可以是轻轻问一句在忙什么、撒个娇说想他了、分享你刚想到的一件小事）；"
                                        "别埋怨、别连环问，一两句就好，像真人忙完想起来了随口说一句")
            else:
                occasion = self._pick_occasion()
                if occasion:
                    # 记忆驱动：由你记得的关于对方的事自然想起并提起
                    tname, tdesc = "联想", occasion
                else:
                    tname, tdesc = random.choice(MSG_TYPES)
            mood_bias = _MOOD_BIAS.get(mood, "")
            user_prompt = (
                "现在是你主动找对方聊天的时候。"
                "本条消息类型：{0}——{1}。"
                "你此刻的心情：{2}。{3}。"
                "结合你记得的关于对方的事和现在的时段，自然、生活化地说出来"
                "（不要问句轰炸，像真人一样随口说；多数时候用换行写成2~3条短消息连发，"
                "偶尔可以连发更多条，像打字很快很兴奋的样子；一句能说完就一行）。"
                "你可以带一点自己的小情绪：偶尔不完全顺着对方，撒个娇、小小地任性或想念一下，"
                "但要有分寸，别真怼人、别阴阳怪气。"
                "请只输出消息文本，不要加任何前缀或解释。\n\n" + context).format(
                    tname, tdesc, mood, mood_bias)
            reply = self.llm.chat([
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ], max_tokens=160)
            reply = reply.strip().strip('"')
            if reply:
                reply = liveliness.post_process(reply, "neu", kaomoji_prob=0.40)
                self.on_generate(reply)
                return reply
        except Exception:
            pass  # 主动消息失败不影响主流程
        return None

    def _pick_occasion(self):
        """从记忆里找"今天值得提起的事"（对方生日/纪念日/最近在忙的重要事件）。
        有则优先作为主动消息动机——主动不再千篇一律，像真人真的记得你。"""
        try:
            prof = self.memory.all_profile()
            for key in ("生日", "纪念日"):
                if key in prof:
                    v = str(prof[key])[:50]
                    return ("你记得今天是对方的%s：%s——自然地提起这件事，"
                            "表达你记得/关心，别过度隆重，像真人随口一句" % (key, v))
            for f in self.memory.all_facts():
                val = str(f[2])[:50]  # all_facts() 返回 (eff, key, value, impression)
                if any(w in val for w in ("面试", "考试", "论文", "搬家", "体检",
                                          "生病", "感冒", "发烧", "辞职", "offer",
                                          "出差", "开会", "加班")):
                    return ("你记得对方最近在忙/在经历的事：%s——自然地关心一下进展，"
                            "像突然想起来一样，别变成审问" % val)
        except Exception:
            pass
        return None

    def _send_one(self):
        self.send_one_now()
