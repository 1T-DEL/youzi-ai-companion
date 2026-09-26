# -*- coding: utf-8 -*-
"""QQ 官方机器人接入（api-v2，WebSocket 方式）。
流程：getAppAccessToken 换凭证 → /gateway/bot 拿网关地址 → 连 WS → Op2 Identify(QQBot token) → 收事件 → SAPI 回复。
文档：https://bot.q.qq.com/wiki/develop/api-v2/
"""
import json
import os
import random
import threading
import time

import requests


class QQOfficial:
    TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
    GATEWAY_URL = "https://api.sgroup.qq.com/gateway/bot"
    API_BASE = "https://api.sgroup.qq.com"

    def __init__(self, cfg, handle_text, log=None, state_file=None):
        self.app_id = (cfg.get("QQ_APP_ID") or "").strip()
        self.secret = (cfg.get("QQ_CLIENT_SECRET") or "").strip()
        self.sandbox = str(cfg.get("QQ_SANDBOX") or "").lower() in ("1", "true", "on", "yes")
        self.intents = int(cfg.get("QQ_INTENTS") or 33554432)
        self.handle_text = handle_text  # callable(text, sender_id) -> reply text or None
        self.log = log or print
        self._stop = threading.Event()
        self._th = None
        self._token = ""
        self._token_expires = 0.0
        self._seq = 0
        self._hb_ms = 45000
        self._session_id = None
        self._identified = False
        self._state_file = state_file
        self.last_contact = self._load_contact()

    # ---- 最近联系人（供主动问候定位目标） ----
    def _load_contact(self):
        if not self._state_file:
            return None
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) and d.get("openid") else None
        except Exception:
            return None

    def _save_contact(self):
        if not self._state_file or not self.last_contact:
            return
        try:
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(self.last_contact, f, ensure_ascii=False)
        except Exception:
            pass

    def enabled(self):
        return bool(self.app_id and self.secret)

    def start(self):
        if not self.enabled():
            return
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()

    def stop(self):
        self._stop.set()

    # ---- 1. 换 access_token（DNS 失败自动重试） ----
    def _fetch_token(self):
        last_err = None
        for i in range(3):
            try:
                resp = requests.post(self.TOKEN_URL,
                                     json={"appId": self.app_id, "clientSecret": self.secret},
                                     timeout=15)
                resp.raise_for_status()
                data = resp.json()
                inner = data.get("data") if isinstance(data.get("data"), dict) else data
                token = inner.get("access_token") or data.get("access_token") or ""
                if not token:
                    raise RuntimeError("换取 token 失败：" + json.dumps(data, ensure_ascii=False))
                try:
                    expires = int(inner.get("expires_in") or data.get("expires_in") or 7200)
                except Exception:
                    expires = 7200
                # 提前 120 秒视为过期，避免临界窗口
                self._token_expires = time.time() + max(60, expires - 120)
                return token
            except Exception as e:
                last_err = e
                time.sleep(2)
        raise last_err

    def _ensure_token(self):
        """token 快过期时刷新一次（连接保持期间的 HTTP 发送用）"""
        if time.time() >= self._token_expires:
            try:
                self._token = self._fetch_token()
                return True
            except Exception:
                return False
        return False

    # ---- 2. 拿网关地址 ----
    def _fetch_gateway(self):
        resp = requests.get(self.GATEWAY_URL,
                            headers={"Authorization": "QQBot " + self._token},
                            timeout=15)
        resp.raise_for_status()
        return resp.json().get("url", "wss://api.sgroup.qq.com/websocket")

    # ---- 主循环 ----
    def _loop(self):
        try:
            import websocket  # websocket-client
        except Exception:
            self.log("[QQ官方] 未安装 websocket-client，跳过（pip install websocket-client）")
            return
        while not self._stop.is_set():
            try:
                self._token = self._fetch_token()
                url = self._fetch_gateway()
                self._run(websocket, url)
            except Exception as e:
                self.log("[QQ官方] 连接异常：" + str(e))
            self._stop.wait(5)

    def _run(self, websocket, url):
        ws = websocket.create_connection(url, timeout=30)
        self.log("[QQ官方] 已连接网关 " + url)
        self._identified = False
        next_hb = time.time() + (self._hb_ms / 1000) * 0.6
        while not self._stop.is_set():
            # 用剩余到心跳的时间作为 recv 超时，保证心跳准时发出
            remain = max(1.0, next_hb - time.time()) if self._identified else 1.0
            ws.settimeout(min(remain, 30))
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                raw = None
            except Exception:
                break
            if raw:
                try:
                    ev = json.loads(raw)
                except Exception:
                    ev = None
                if ev:
                    self._on_payload(ev, ws)
            # 连接期间 token 快过期就后台刷新（HTTP 发送用）
            self._ensure_token()
            # 发送 Op2 Identify（直到 READY 前反复尝试）
            if not self._identified and time.time() >= next_hb:
                try:
                    ws.send(json.dumps({
                        "op": 2,
                        "d": {
                            "token": "QQBot " + self._token,
                            "intents": self.intents,
                            "shard": [0, 1],
                            "properties": {"$os": "windows", "$browser": "doubao",
                                           "$device": "pc"},
                        },
                    }))
                    next_hb = time.time() + 1.0
                except Exception:
                    break
            # 心跳：op1，d=最新 seq（提前发送，保持会话存活）
            elif self._identified and time.time() >= next_hb:
                try:
                    ws.send(json.dumps({"op": 1, "d": self._seq or None}))
                    self.log("[QQ官方] 发心跳 d={}".format(self._seq))
                    next_hb = time.time() + (self._hb_ms / 1000) * 0.6
                except Exception:
                    break
        self.log("[QQ官方] 连接断开，将重连")
        try:
            ws.close()
        except Exception:
            pass

    def _on_payload(self, ev, ws):
        op = ev.get("op")
        if op == 10:
            d = ev.get("d") or {}
            self._hb_ms = int(d.get("heartbeat_interval") or 45000)
            self.log("[QQ官方] Hello：心跳间隔 {}ms".format(self._hb_ms))
            return
        if ev.get("s") is not None:
            self._seq = ev.get("s")
        if op == 0:
            t = ev.get("t")
            if t == "READY":
                d = ev.get("d") or {}
                self._session_id = d.get("session_id")
                self._identified = True
                u = d.get("user") or {}
                self.log("[QQ官方] 登录成功 | session=" + str(self._session_id))
                self.log("[QQ官方] 当前在线机器人 → 名字：" + str(u.get("username")) +
                         " | 账号ID：" + str(u.get("id")))
            elif t in ("GROUP_AT_MESSAGE_CREATE", "C2C_MESSAGE_CREATE",
                       "FRIEND_AT_MESSAGE_CREATE"):
                self._on_message(ev.get("d") or {})
        elif op == 11:
            self.log("[QQ官方] 心跳ACK")  # Heartbeat ACK

    def _extract_text(self, d):
        content = d.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for seg in content:
                if seg.get("type") == "text":
                    parts.append(seg.get("data", {}).get("content", ""))
            return "".join(parts)
        return ""

    def _on_message(self, d):
        text = self._extract_text(d).strip()
        self.log("[QQ官方] 收到消息：{}".format(text[:20] or "(空)"))
        if not text:
            return
        author = d.get("author") or {}
        target = {"id": d.get("id"), "seq": d.get("msg_seq")}
        if "group_openid" in d:  # 群@消息
            target["channel"] = "group"
            target["openid"] = d.get("group_openid")
            member = author.get("member_openid")
            sender = "qq:" + (member or target["openid"])
            target["member"] = member
        else:  # 单聊 C2C
            target["channel"] = "c2c"
            openid = author.get("id") or author.get("openid") or ""
            target["openid"] = openid
            sender = "qq:" + openid
        if not target.get("openid"):
            return
        # 记录最近联系人（供主动问候用）
        self.last_contact = {"openid": target["openid"], "channel": target["channel"]}
        self._save_contact()
        reply = self.handle_text(text, sender)
        if reply:
            self._send_handle_reply(target, reply)

    # ---- 把回复发出去：人设有时会连发多条，按行拆成多条短消息 ----
    def _send_handle_reply(self, target, reply):
        lines = [ln.strip() for ln in str(reply).split("\n") if ln.strip()]
        if len(lines) > 1 and random.random() < 0.7:
            # 连发：2~3 条为佳，最多发 3 条
            for ln in lines[:3]:
                self.send_reply(target, ln)
                time.sleep(0.4)
        else:
            self.send_reply(target, lines[0] if lines else reply)

    # ---- 发送被动回复 ----
    def send_reply(self, target, text):
        if not target or not text or not target.get("openid"):
            return
        # msg_type=0 纯文本：content 必须是纯文本字符串
        body = {
            "content": text,
            "msg_type": 0,
            "msg_seq": int(time.time() * 1000) % 100000000,
            "msg_id": target.get("id") or "",
        }
        try:
            if target["channel"] == "group":
                self._post_json("/v2/groups/{}/messages".format(target["openid"]),
                                body, headers=self._auth_headers(), add_author=target.get("member") or "")
            else:
                # 单聊（C2C）被动回复的正确接口
                self._post_json("/v2/users/{}/messages".format(target["openid"]),
                                body, headers=self._auth_headers())
        except Exception as e:
            self.log("[QQ官方] 回复发送失败：" + str(e))

    # ---- 发送主动消息（主动问候，无需 msg_id） ----
    def send_active(self, text, openid=None, channel="c2c"):
        if not text:
            return
        openid = openid or (self.last_contact or {}).get("openid")
        if not openid:
            self.log("[QQ官方] 主动消息跳过：还没有可发对象")
            return
        body = {
            "content": text,
            "msg_type": 0,
            "msg_seq": int(time.time() * 1000) % 100000000,
        }
        try:
            if channel == "group":
                self._post_json("/v2/groups/{}/messages".format(openid),
                                body, headers=self._auth_headers(), add_author="")
            else:
                self._post_json("/v2/users/{}/messages".format(openid),
                                body, headers=self._auth_headers())
            self.log("[QQ官方] 主动消息已发送：" + text[:30])
        except Exception as e:
            self.log("[QQ官方] 主动消息发送失败：" + str(e))

    def _auth_headers(self):
        return {"Authorization": "QQBot " + self._token, "Content-Type": "application/json"}

    # ---- 带主机容错的 POST：sgroup 优先，超时/异常则回退 bot.qq.com ----
    def _post_json(self, path, body, headers, add_author=None):
        if add_author:
            body = dict(body, author=add_author)
        last_err = None
        for attempt in range(2):  # 第一遍正常，401 时刷新 token 再试一遍
            for host in (self.API_BASE, "https://api.bot.qq.com"):
                try:
                    resp = requests.post(host + path, headers=headers, json=body, timeout=20)
                    if resp.status_code == 401 and attempt == 0:
                        # token 过期：刷新后重试
                        if self._ensure_token():
                            headers["Authorization"] = "QQBot " + self._token
                        break
                    resp.raise_for_status()
                    return resp
                except Exception as e:
                    last_err = e
        raise last_err
