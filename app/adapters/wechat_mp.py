# -*- coding: utf-8 -*-
"""微信公众号接入：回调校验 + 被动回复。
注意：公众号被动回复受 5 秒硬限制，回复必须在超时前返回。"""
import hashlib
import xml.etree.ElementTree as ET


def check_signature(token, signature, timestamp, nonce):
    if not signature:
        return False
    arr = sorted([token, timestamp, nonce])
    s = hashlib.sha1("".join(arr).encode("utf-8")).hexdigest()
    return s == signature


def parse_msg(body):
    """解析微信文本消息 XML，返回 (openid, content)。"""
    try:
        root = ET.fromstring(body)
        to = (root.findtext("ToUserName") or "").strip()
        fr = (root.findtext("FromUserName") or "").strip()
        msg_type = (root.findtext("MsgType") or "").strip()
        content = (root.findtext("Content") or "").strip()
        return to, fr, msg_type, content
    except Exception:
        return "", "", "", ""


def build_reply(to_user, from_user, text):
    import xml.sax.saxutils as su
    text = su.escape(text)
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{from_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{to_user}]]></FromUserName>"
        "<CreateTime>" + str(int(__import__('time').time())) + "</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{text}]]></Content>"
        "</xml>"
    )
