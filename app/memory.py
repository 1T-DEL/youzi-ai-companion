# -*- coding: utf-8 -*-
"""长期记忆：SQLite 存储聊天记录与长期事实，线程安全。
v2：事实带「记忆强度」+ 遗忘曲线（艾宾浩斯式衰减，被再次提及会加强）。
另存好感度(affection)与性格状态(traits)两张表，供上层模块读写。
"""
import os
import sqlite3
import time
import math
import threading

DEFAULT_HALF_LIFE = 7 * 86400.0  # 记忆半衰期：7 天


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
                strength REAL DEFAULT 1.0, accesses INTEGER DEFAULT 0, last_access REAL)""")
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

    # ---- 长期事实：带遗忘曲线 ----
    def set_fact(self, key, value):
        """存/更新一个长期记忆。若同一 key 再次被提及（reinforce 式更新）则加强。"""
        now = time.time()
        with self._lock:
            c = self._conn()
            row = c.execute("SELECT strength FROM facts WHERE key=?".replace("WHERE key=?", "WHERE key=?"), (key,)).fetchone()
            if row:
                # 再次被提及：加强记忆强度
                new_str = min(1.0, float(row[0]) + 0.2)
                c.execute("UPDATE facts SET value=?, ts=?, strength=?, accesses=accesses+1, "
                          "last_access=? WHERE key=?",
                          (str(value), now, new_str, now, key))
            else:
                c.execute("INSERT INTO facts(key, value, ts, strength, accesses, last_access) "
                          "VALUES(?,?,?,1.0,1,?)",
                          (key, str(value), now, now))
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

    def all_facts(self, threshold=0.3, half_life=DEFAULT_HALF_LIFE):
        """返回还没被遗忘的长期记忆 dict（按记忆强度降序）。"""
        now = time.time()
        with self._lock:
            c = self._conn()
            rows = c.execute(
                "SELECT key, value, strength, last_access FROM facts").fetchall()
            c.close()
        out = []
        for key, value, strength, last_access in rows:
            eff = self._effective(strength, last_access, now, half_life)
            if eff >= threshold:
                out.append((eff, key, value))
        out.sort(key=lambda x: -x[0])
        return {k: v for _, k, v in out}

    def fact_stats(self, half_life=DEFAULT_HALF_LIFE):
        """全部长期记忆及其当前记忆强度%（供管理页展示）。"""
        now = time.time()
        with self._lock:
            c = self._conn()
            rows = c.execute(
                "SELECT key, value, strength, accesses, last_access FROM facts").fetchall()
            c.close()
        stats = []
        for key, value, strength, accesses, last_access in rows:
            eff = self._effective(strength, last_access, now, half_life)
            stats.append({"key": key, "value": value, "strength": round(eff * 100, 1),
                          "accesses": accesses})
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
            row = c.execute("SELECT value FROM traits WHERE name=?".replace("WHERE name=?", "WHERE name=?"), (name,)).fetchone()
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
            row = c.execute("SELECT strength FROM profile WHERE key=?".replace("WHERE key=?", "WHERE key=?"), (key,)).fetchone()
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
            c.execute("DELETE FROM profile WHERE key=?".replace("WHERE key=?", "WHERE key=?"), (key,))
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
            row = c.execute("SELECT 1 FROM events WHERE type=? LIMIT 1".replace("WHERE type=?", "WHERE type=?"), (etype,)).fetchone()
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
