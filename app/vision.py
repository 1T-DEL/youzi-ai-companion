# -*- coding: utf-8 -*-
"""图片识别：用阿里千问视觉模型识别图片内容，返回中文描述。
供 OneBot(QQ) 等适配器在收到图片时调用，把"图里是什么"转成文字喂给大模型。
"""
import base64
import json
import os
import re
import requests

_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
_MODEL = "qwen-vl-max"


def _get_key(cfg):
    # cfg 为整个 .env 字典；找不到时尝试读取环境变量
    return cfg.get("DASHSCOPE_API_KEY") or os.environ.get("DASHSCOPE_API_KEY", "")


def resolve_image_bytes(data):
    """从 OneBot image 段 data 中解析出图片字节。
    data 可能含 url(http/https)、file(本地路径)、或 file:// 形式的 url。
    返回 bytes；无法获取返回 None。
    """
    url = data.get("url") or ""
    file_path = data.get("file") or ""
    if url.startswith("file://"):
        url = ""
        file_path = file_path or url[7:]
    bytes_ = None
    if url and (url.startswith("http://") or url.startswith("https://")):
        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 200 and len(r.content) > 100:
                bytes_ = r.content
        except Exception:
            bytes_ = None
    if bytes_ is None and file_path:
        try:
            with open(file_path, "rb") as f:
                bytes_ = f.read()
        except Exception:
            bytes_ = None
    if bytes_ is None and url and not url.startswith("http"):
        # 兜底：当成路径
        try:
            with open(url, "rb") as f:
                bytes_ = f.read()
        except Exception:
            bytes_ = None
    return bytes_


def _mime(b):
    if b[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return "jpeg"


def describe_image(image_bytes, api_key, model=_MODEL, max_tokens=150):
    """识别单张图片，返回简短中文描述；失败返回 None。"""
    if not image_bytes or not api_key:
        return None
    mime = _mime(image_bytes)
    b64 = base64.b64encode(image_bytes).decode()
    body = {
        "model": model,
        "input": {
            "messages": [{
                "role": "user",
                "content": [
                    {"image": "data:image/{};base64,{}".format(mime, b64)},
                    {"text": "请用一句中文简洁描述这张图片的内容，若涉及人物可描述其表情动作与场景。"},
                ],
            }]
        },
        "parameters": {"max_tokens": max_tokens},
    }
    try:
        r = requests.post(_ENDPOINT,
                          headers={"Authorization": "Bearer " + api_key,
                                   "Content-Type": "application/json"},
                          json=body, timeout=60)
        if r.status_code != 200:
            return None
        d = r.json()
        content = d["output"]["choices"][0]["message"]["content"]
        text = ""
        for item in content:
            if item.get("text"):
                text += item["text"]
        return (text or "").strip() or None
    except Exception:
        return None


def describe_from_segment(seg, api_key, model=_MODEL):
    """直接给 OneBot image 段，返回描述或 None。"""
    b = resolve_image_bytes(seg.get("data") or {})
    if not b:
        return None
    return describe_image(b, api_key, model)
