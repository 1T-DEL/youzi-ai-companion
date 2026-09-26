# -*- coding: utf-8 -*-
"""长期记忆：SQLite 存储聊天记录与长期事实，线程安全。
v2：事实带「记忆强度」+ 遗忘曲线（艾宾浩斯式衰减，被再次提及会加强）。
v4：多维记忆（小凌式）—— 意义强度 / 细节完整度 / 情绪残留 分离衰减：
    - 细节衰减最快（λ 由意义、情绪、巩固、干扰共同调节）
    - 意义几乎不衰减（"那个人对我很重要"这种不会忘）
    - 情绪残留持久（细节忘了，当时的感受还在，会改变表达）
    - 记忆重建可信度 confidence：证据(细节)越多越确信，靠情绪撑时只有"感觉"
    - cue_retrieval：线索触发检索（触景生情）——用户文本命中记忆关键词或情绪，
      意义/情绪权重高的旧记忆会突然"浮出来"，供主动追问
另存好感度(affection)与性格状态(traits)两张表，供上层模块读写。
"""
import os
import sqlite3
import time
import math
import threading

DEFAULT_HALF_LIFE = 7 * 86400.0  # 记忆半衰期：7 天（旧版兼容值）

# ---- 多维衰减参数（参考艾宾浩斯 + 巩固/干扰理论） ----
_LAM0 = 0.12          # 细节基线日衰减率（≈5.8 天细节减半）
_ALPHA = 0.55         # 意义对衰减的抑制
_BETA = 0.50          # 情绪残留对衰减的抑制
_GAMMA = 0.35         # 巩固程度对衰减的抑制
_DELTA = 0.60         # 干扰对衰减的加速
_LAM_MEANING_RATIO = 0.12   # 意义衰减率 = 细节的 12%（几乎不忘）
_LAM_EMOTION_RATIO = 0.30   # 情绪残留衰减率 = 细节的 30%（持久）


class Memory:
    def __init__(self, db_path):
        self.db_path = db_path
        if os.path.dirname(db_path):
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _init(self):
        with self._lock:
            c = self._conn()
            c.execute("""CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT, content TEXT, ts REAL, meta TEXT DEFAULT '')""")
            c.execute("""CREATE TABLE IF NOT EXISTS facts(
                key TEXT PRIMARY KEY, value TEXT, ts REAL,
                strength REAL DEFAULT 1.0, accesses INTEGER DEFAULT 0, last_access REAL,
                meaning REAL DEFAULT 0.6, detail REAL DEFAULT 0.8,
                emotion_residue REAL DEFAULT 0.3, consolidation REAL DEFAULT 0.5,
                interference REAL DEFAULT 0.2)""")
            c.execute("""CREATE TABLE IF NOT EXISTS affection(
                id INTEGER PRIMARY KEY CHECK(id=1),
                score REAL DEFAULT 40, updated_ts REAL, messages INTEGER DEFAULT 0)""")
            c.execute("""CREATE TABLE IF NOT EXISTS traits(
                name TEXT PRIMARY KEY, value REAL, updated_ts REAL)""")
            # v3：结构化画像 / 关系事件 / 对话摘要归档 / 好感度历史
            c.execute("""CREATE TABLE IF NOT EXISTS profile(
                key TEXT PRIMARY KEY, value TEXT, ts REAL, strength REAL DEFAULT 1.0, accesses INTEGER DEFAULT 1)""")
            c.execute("""CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, type TEXT, summary TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS summaries(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, summary TEXT, msg_from INTEGER DEFAULT 0)""")
            c.execute("""CREATE TABLE IF NOT EXISTS affection_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, score REAL)""")
            # 旧库迁移：补列
            try:
                c.execute("ALTER TABLE facts ADD COLUMN strength REAL DEFAULT 1.0")
            except Exception:
                pass
            try:
                c.execute("ALTER TABLE facts ADD COLUMN accesses INTEGER DEFAULT 0")
            except Exception:
                pass
            try:
                c.execute("ALTER TABLE facts ADD COLUMN last_access REAL")
            except Exception:
                pass
            # v4：多维记忆列（旧库迁移补列）
            for col, dflt in (("meaning", "0.6"), ("detail", "0.8"),
                              ("emotion_residue", "0.3"), ("consolidation", "0.5"),
                              ("interference", "0.2")):
                try:
                    c.execute("ALTER TABLE facts ADD COLUMN %s REAL DEFAULT %s" % (col, dflt))
                except Exception:
                    pass
            c.commit()
            c.close()

    # ---- 聊天记录 ----
    def add_message(self, role, content, meta=""):
        with self._lock:
            c = self._conn()
            c.execute("INSERT INTO messages(role, content, ts, meta) VALUES(?,?,?,?)",
                      (role, content, time.time(), meta))
            c.commit()
            c.close()

    def count_messages(self):
        """messages 表当前最大 id（用于摘要归档的进度锚点）。"""
        with self._lock:
            c = self._conn()
            row = c.execute("SELECT MAX(id) FROM messages").fetchone()
            c.close()
        return int(row[0] or 0)

    def messages_since(self, msg_id):
        """返回 id > msg_id 的全部历史（供摘要归档压缩）。"""
        with self._lock:
            c = self._conn()
            rows = c.execute(
                "SELECT role, content FROM messages WHERE id > ? ORDER BY id", (int(msg_id),)).fetchall()
            c.close()
        return rows

    def recent(self, n):
        with self._lock:
            c = self._conn()
            rows = c.execute(
                "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (n,)).fetchall()
            c.close()
        return list(reversed(rows))

    def all_today(self):
        with self._lock:
            c = self._conn()
            day = time.time() - 86400
            rows = c.execute(
                "SELECT role, content FROM messages WHERE ts >= ? ORDER BY id", (day,)).fetchall()
            c.close()
        return rows

    def clear(self):
        with self._lock:
            c = self._conn()
            c.execute("DELETE FROM messages")
            c.execute("DELETE FROM facts")
            c.execute("DELETE FROM affection")
            c.execute("DELETE FROM traits")
            c.execute("DELETE FROM profile")
            c.execute("DELETE FROM events")
            c.execute("DELETE FROM summaries")
            c.execute("DELETE FROM affection_history")
            c.commit()
            c.close()

    # ---- 长期事实：带遗忘曲线（v4 多维） ----
    def set_fact(self, key, value, meaning=None, detail=None, emotion=None):
        """存/更新一个长期记忆。若同一 key 再次被提及（reinforce 式更新）则加强。

        meaning: 意义强度 0~1（对你有多重要）；detail: 细节完整度 0~1；
        emotion: 情绪残留 0~1（当时感受还记得多少）。不传时按旧值/默认值处理。
        """
        now = time.time()
        with self._lock:
            c = self._conn()
            row = c.execute(
                "SELECT strength, meaning, detail, emotion_residue, consolidation, "
                "interference FROM facts WHERE key=?", (key,)).fetchone()
            if row:
                old_str, old_m, old_d, old_e, old_c, old_i = row
                # 再次被提及：记忆被"重新巩固"——细节与意义加强、干扰略升
                new_str = min(1.0, float(old_str) + 0.2)
                new_m = min(1.0, float(old_m) + 0.12)
                new_d = min(1.0, float(old_d) + 0.18)
                new_e = max(float(old_e), emotion if emotion is not None else float(old_e))
                new_c = min(1.0, float(old_c) + 0.1)
                new_i = min(0.9, float(old_i) + 0.04)
                c.execute(
                    "UPDATE facts SET value=?, ts=?, strength=?, accesses=accesses+1, "
                    "last_access=?, meaning=?, detail=?, emotion_residue=?, "
                    "consolidation=?, interference=? WHERE key=?",
                    (str(value), now, new_str, now, new_m, new_d, new_e, new_c, new_i, key))
            else:
                m = 0.6 if meaning is None else float(meaning)
                d = 0.8 if detail is None else float(detail)
                e = 0.3 if emotion is None else float(emotion)
                c.execute(
                    "INSERT INTO facts(key, value, ts, strength, accesses, last_access, "
                    "meaning, detail, emotion_residue, consolidation, interference) "
                    "VALUES(?,?,?,1.0,1,?,?,?,?,0.5,0.2)",
                    (key, str(value), now, now, m, d, e))
            c.commit()
            c.close()

    def reinforce_fact(self, key):
        now = time.time()
        with self._lock:
            c = self._conn()
            c.execute("UPDATE facts SET strength=MIN(1.0,strength+0.2), "
                      "last_access=?, accesses=accesses+1 WHERE key=?", (now, key))
            c.commit()
            c.close()

    def delete_fact(self, key):
        with self._lock:
            c = self._conn()
            c.execute("DELETE FROM facts WHERE key=?", (key,))
            c.commit()
            c.close()

    def _effective(self, strength, last_access, now, half_life):
        if last_access is None:
            return strength
        age = max(0.0, now - last_access)
        decay = math.exp(-age / half_life)
        return max(0.0, min(1.0, strength * decay))

    # ---- v4：多维衰减（意义 / 细节 / 情绪残留 分离） ----
    def _decay_multi(self, meaning, detail, emotion, consolidation, interference,
                     last_access, now):
        """按小凌式记忆模型衰减，返回 (eff, meaning_eff, detail_eff, emotion_eff, confidence)。

        λ = λ0·(1-α·意义)·(1-β·情绪)·(1-γ·巩固)·(1+δ·干扰)
        细节按 λ 衰减（忘细节快）；意义按 12%λ（几乎不忘）；
        情绪残留按 30%λ（持久，细节没了感受还在）。
        confidence = 证据占比：细节越完整越"确定"，只剩情绪时只有"感觉"。
        """
        if last_access is None:
            last_access = now
        age_days = max(0.0, (now - last_access) / 86400.0)
        lam = _LAM0 * (1 - _ALPHA * meaning) * (1 - _BETA * emotion) \
              * (1 - _GAMMA * consolidation) * (1 + _DELTA * interference)
        lam = max(0.005, lam)
        detail_eff = detail * math.exp(-lam * age_days)
        meaning_eff = meaning * math.exp(-lam * _LAM_MEANING_RATIO * age_days)
        emotion_eff = emotion * math.exp(-lam * _LAM_EMOTION_RATIO * age_days)
        eff = min(1.0, 0.45 * meaning_eff + 0.35 * detail_eff + 0.2 * emotion_eff)
        # 记忆重建可信度：证据(细节)越多越确信；靠情绪撑时置信度低
        evidence = detail_eff + 0.05
        confidence = min(1.0, evidence / (evidence + 0.25 + emotion_eff * 0.15))
        return eff, meaning_eff, detail_eff, emotion_eff, confidence

    @staticmethod
    def _impression(eff, meaning_eff, detail_eff, emotion_eff, confidence):
        """把记忆状态翻译成可注入提示词的"印象"标签（人会怎么说）。"""
        if confidence >= 0.65 and meaning_eff >= 0.5:
            return "记得很清楚"
        if meaning_eff >= 0.6 and detail_eff < 0.35 and emotion_eff >= 0.45:
            return "记不太清了，但还记得那种感觉"
        if confidence < 0.4:
            return "只有一点模糊的印象"
        if eff >= 0.55:
            return "印象挺深"
        return "有点模糊了"

    def _facts_rows(self):
        with self._lock:
            c = self._conn()
            rows = c.execute(
                "SELECT key, value, meaning, detail, emotion_residue, consolidation, "
                "interference, last_access, accesses FROM facts").fetchall()
            c.close()
        return rows

    def all_facts(self, threshold=0.3, half_life=DEFAULT_HALF_LIFE):
        """返回还没被遗忘的长期记忆 dict（按记忆强度降序）。

        v4：值带「印象」标签，供 prompt 注入（"记得很清楚/记不太清了"）。
        """
        now = time.time()
        out = []
        for key, value, meaning, detail, emotion, consol, interf, last_access, _acc in self._facts_rows():
            eff, m_eff, d_eff, e_eff, conf = self._decay_multi(
                meaning, detail, emotion, consol, interf, last_access, now)
            if eff >= threshold:
                out.append((eff, key, value,
                            self._impression(eff, m_eff, d_eff, e_eff, conf)))
        out.sort(key=lambda x: -x[0])
        return {k: v for _, k, v, _ in out}

    def all_facts_detailed(self, threshold=0.2, half_life=DEFAULT_HALF_LIFE):
        """多维版本：返回 (eff, key, value, impression, meaning_eff, detail_eff,
        emotion_eff, confidence) 排序列表，供 cue 检索与管理页。"""
        now = time.time()
        out = []
        for key, value, meaning, detail, emotion, consol, interf, last_access, _acc in self._facts_rows():
            eff, m_eff, d_eff, e_eff, conf = self._decay_multi(
                meaning, detail, emotion, consol, interf, last_access, now)
            if eff >= threshold:
                out.append((eff, key, value,
                            self._impression(eff, m_eff, d_eff, e_eff, conf),
                            m_eff, d_eff, e_eff, conf))
        out.sort(key=lambda x: -x[0])
        return out

    def cue_retrieval(self, user_text, top=2, threshold=0.15):
        """线索触发检索（触景生情）：用户文本命中记忆关键词 / 情绪时，
        意义或情绪权重高的旧记忆即使平时不显眼也会"浮出来"。

        返回 [ {key, value, meaning_eff, emotion_eff, impression, reason} ]，
        供上层在回复提示词里注入"你突然想起了…"，形成自然追问。
        """
        if not user_text or not user_text.strip():
            return []
        now = time.time()
        # 线索词：情绪 + 常见实体/话题（覆盖"猫→橘猫年糕"这类跨表述命中）
        HINTS = ["难过", "伤心", "想哭", "哭", "委屈", "累", "好累", "烦", "焦虑",
                 "失眠", "难受", "疼", "痛", "emo", "孤独", "孤单", "丧", "崩溃",
                 "想你", "爱你", "开心", "高兴", "喜欢", "抱抱", "生日", "纪念日",
                 "加班", "吃饭", "下雨", "回家", "猫", "狗", "考试", "面试", "论文",
                 "毕业", "火锅", "奶茶", "电影", "游戏", "音乐", "旅行", "家",
                 "妈妈", "爸爸", "同学", "室友", "同事", "领导", "感冒", "发烧",
                 "咳嗽", "体检", "搬家", "约会", "分手", "冷", "热", "困", "饿",
                 "上班", "开会", "项目", "offer", "涨工资", "中奖"]
        hint = next((w for w in HINTS if w in user_text), None)
        hits = []
        for key, value, meaning, detail, emotion, consol, interf, last_access, _acc in self._facts_rows():
            eff, m_eff, d_eff, e_eff, conf = self._decay_multi(
                meaning, detail, emotion, consol, interf, last_access, now)
            if eff < threshold and e_eff < 0.35:
                continue
            reason = None
            score = 0.0
            # 1) 线索词命中该记忆的内容（触景生情：一句话带出旧事）
            if hint and (hint in key or (value and hint in value)):
                score = 0.4 + e_eff * 0.4 + m_eff * 0.2
                reason = hint
            # 2) 情绪线索命中且该记忆情绪残留高 → 那种感觉浮上来
            if hint in ("难过", "伤心", "哭", "委屈", "累", "烦", "焦虑", "失眠",
                        "难受", "疼", "痛", "emo", "孤独", "孤单", "丧", "崩溃") \
                    and e_eff >= 0.45:
                cand = 0.35 + e_eff * 0.4
                if cand > score:
                    score = cand
                    reason = hint
            # 3) 文本整词/2字n-gram兜底（覆盖词表外的词）
            if score <= 0:
                for w in [x for x in user_text.strip().split() if len(x) >= 2]:
                    if w in key or (value and w in value):
                        score = 0.4
                        reason = w
                        break
            if score <= 0:
                continue
            score += m_eff * 0.15
            hits.append((score, {
                "key": key, "value": value, "meaning_eff": round(m_eff, 2),
                "emotion_eff": round(e_eff, 2),
                "impression": self._impression(eff, m_eff, d_eff, e_eff, conf),
                "reason": reason or "线索",
            }))
        hits.sort(key=lambda x: -x[0])
        return [h for _, h in hits[:top]]

    def fact_stats(self, half_life=DEFAULT_HALF_LIFE):
        """全部长期记忆及其当前记忆状态（多维，供管理页展示）。"""
        now = time.time()
        stats = []
        for key, value, meaning, detail, emotion, consol, interf, last_access, accesses in self._facts_rows():
            eff, m_eff, d_eff, e_eff, conf = self._decay_multi(
                meaning, detail, emotion, consol, interf, last_access, now)
            stats.append({"key": key, "value": value,
                          "strength": round(eff * 100, 1),
                          "meaning": round(m_eff * 100, 1),
                          "detail": round(d_eff * 100, 1),
                          "emotion": round(e_eff * 100, 1),
                          "confidence": round(conf * 100, 1),
                          "impression": self._impression(eff, m_eff, d_eff, e_eff, conf),
                          "accesses": int(accesses)})
        return stats

    def count_facts(self, threshold=0.3, half_life=DEFAULT_HALF_LIFE):
        return len(self.all_facts(threshold, half_life))

    # ---- 好感度 ----
    def get_affection(self):
        with self._lock:
            c = self._conn()
            row = c.execute("SELECT score, updated_ts, messages FROM affection WHERE id=1").fetchone()
            c.close()
        if row:
            return {"score": float(row[0]), "updated_ts": float(row[1]), "messages": int(row[2])}
        return None

    def set_affection(self, score, messages=None, updated_ts=None):
        now = updated_ts or time.time()
        cur = self.get_affection()
        msgs = messages if messages is not None else (cur["messages"] if cur else 0)
        with self._lock:
            c = self._conn()
            c.execute("INSERT INTO affection(id, score, updated_ts, messages) VALUES(1,?,?,?) "
                      "ON CONFLICT(id) DO UPDATE SET score=?, updated_ts=?, messages=?",
                      (score, now, msgs, score, now, msgs))
            c.commit()
            c.close()

    # ---- 性格状态 ----
    def get_trait(self, name, default=40.0):
        with self._lock:
            c = self._conn()
            row = c.execute("SELECT value FROM traits WHERE name=?", (name,)).fetchone()
            c.close()
        return float(row[0]) if row else default

    def all_traits(self):
        with self._lock:
            c = self._conn()
            rows = c.execute("SELECT name, value, updated_ts FROM traits").fetchall()
            c.close()
        return {n: {"value": round(float(v), 1), "updated_ts": float(t)} for n, v, t in rows}

    def set_trait(self, name, value):
        with self._lock:
            c = self._conn()
            c.execute("INSERT INTO traits(name, value, updated_ts) VALUES(?,?,?) "
                      "ON CONFLICT(name) DO UPDATE SET value=?, updated_ts=?",
                      (name, float(value), time.time(), float(value), time.time()))
            c.commit()
            c.close()

    # ---- v3：结构化画像（多次提及加强，像真正的"档案"） ----
    def set_profile(self, key, value):
        """存/更新画像字段。同一字段再次被提及时加强（越常提记得越牢）。"""
        now = time.time()
        value = str(value)[:200]
        with self._lock:
            c = self._conn()
            row = c.execute("SELECT strength FROM profile WHERE key=?", (key,)).fetchone()
            if row:
                new_str = min(1.0, float(row[0]) + 0.25)
                c.execute("UPDATE profile SET value=?, ts=?, strength=?, accesses=accesses+1 "
                          "WHERE key=?", (value, now, new_str, key))
            else:
                c.execute("INSERT INTO profile(key, value, ts, strength, accesses) VALUES(?,?,?,1.0,1)",
                          (key, value, now))
            c.commit()
            c.close()

    def all_profile(self):
        """返回画像字段 dict（按最近提及降序）。"""
        with self._lock:
            c = self._conn()
            rows = c.execute("SELECT key, value, ts FROM profile ORDER BY ts DESC").fetchall()
            c.close()
        return {k: v for k, v, _ in rows}

    def delete_profile(self, key):
        with self._lock:
            c = self._conn()
            c.execute("DELETE FROM profile WHERE key=?", (key,))
            c.commit()
            c.close()

    # ---- v3：关系事件时间线 ----
    def add_event(self, etype, summary):
        now = time.time()
        with self._lock:
            c = self._conn()
            c.execute("INSERT INTO events(ts, type, summary) VALUES(?,?,?)",
                      (now, str(etype), str(summary)[:200]))
            c.commit()
            c.close()

    def has_event_type(self, etype):
        with self._lock:
            c = self._conn()
            row = c.execute("SELECT 1 FROM events WHERE type=? LIMIT 1", (etype,)).fetchone()
            c.close()
        return row is not None

    def list_events(self, limit=30):
        with self._lock:
            c = self._conn()
            rows = c.execute(
                "SELECT ts, type, summary FROM events ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
            c.close()
        return [{"ts": float(t), "type": ty, "summary": s} for t, ty, s in rows]

    # ---- v3：对话摘要归档（懒触发压缩，见 main.py） ----
    def add_summary(self, summary, msg_from=0):
        with self._lock:
            c = self._conn()
            c.execute("INSERT INTO summaries(ts, summary, msg_from) VALUES(?,?,?)",
                      (time.time(), str(summary)[:800], int(msg_from)))
            c.commit()
            c.close()

    def latest_summary(self):
        with self._lock:
            c = self._conn()
            row = c.execute("SELECT ts, summary, msg_from FROM summaries ORDER BY id DESC LIMIT 1").fetchone()
            c.close()
        if row:
            return {"ts": float(row[0]), "summary": row[1], "msg_from": int(row[2])}
        return None

    # ---- v3：好感度历史（供管理页画成长曲线） ----
    def record_affection(self, score):
        with self._lock:
            c = self._conn()
            c.execute("INSERT INTO affection_history(ts, score) VALUES(?,?)", (time.time(), float(score)))
            c.commit()
            c.close()

    def affection_history(self, n=120):
        with self._lock:
            c = self._conn()
            rows = c.execute(
                "SELECT ts, score FROM affection_history ORDER BY id DESC LIMIT ?", (n,)).fetchall()
            c.close()
        return [{"ts": float(t), "score": float(s)} for t, s in reversed(rows)]
