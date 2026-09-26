# -*- coding: utf-8 -*-
"""读取 .env 配置文件的极简解析器（兼容 setup.html 生成的格式）。"""
import os


DEFAULTS = {
    "APP_NAME": "电子女友",
    "HOST": "127.0.0.1",
    "PORT": "8000",
    "DATABASE_PATH": "data/pimate.db",
    "PERSONA_PATH": "personas/youzi.yaml",
    "LLM_BASE_URL": "",
    "LLM_API_KEY": "",
    "LLM_MODEL": "",
    "LLM_TEMPERATURE": "0.75",
    "LLM_MAX_TOKENS": "500",
    "LLM_TIMEOUT_SECONDS": "45",
    "HISTORY_LIMIT": "16",
    "RATE_LIMIT_PER_MINUTE": "30",
    "ASR_MODEL": "",
    "TTS_MODEL": "",
    "TTS_VOICE": "nova",
    "VOICE_REPLY_PROB": "0.25",
    "SPEECH_BACKEND": "openai",
    "DASHSCOPE_API_KEY": "",
    "VISION_MODEL": "qwen-vl-max",
    "VIDEO_COOKIES_FILE": "",
    "MEMORY_THRESHOLD": "0.3",
    "MEMORY_HALF_LIFE_DAYS": "7",
    "ALLOWLIST": "",
    "QQ_ENABLED": "false",
    "QQ_APP_ID": "",
    "QQ_CLIENT_SECRET": "",
    "QQ_SANDBOX": "false",
    "QQ_INTENTS": "33554432",
    "ONEBOT_ENABLED": "false",
    "ONEBOT_WS_URL": "ws://127.0.0.1:3001",
    "ONEBOT_ACCESS_TOKEN": "",
    "WECHAT_TOKEN": "",
    "WECHAT_REPLY_TIMEOUT_SECONDS": "4.5",
    "WECHAT_GATEWAY_ENABLED": "false",
    "WECHAT_GATEWAY_OUTBOUND_URL": "",
    "WECHAT_GATEWAY_SECRET": "",
    "PROACTIVE_ENABLED": "false",
    "PROACTIVE_INTERVAL_MINUTES": "180",
    "PROACTIVE_QUIET_START": "23",
    "PROACTIVE_QUIET_END": "8",
    "KAOMOJI_PROB": "0.40",
    "STICKER_PROB": "0.12",
    "STICKER_GIF_PROB": "0.70",
    "STICKER_COOLDOWN_MIN": "15",
}


def load_dotenv(path):
    """读取 .env，返回 dict。找不到文件时返回 {}。"""
    data = {}
    if not path or not os.path.exists(path):
        return data
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            data[k] = v
    return data


def merged(path):
    """合并默认值与 .env 覆盖，返回 dict。"""
    env = load_dotenv(path)
    out = dict(DEFAULTS)
    out.update({k: v for k, v in env.items() if v != ""})
    return out


def to_bool(v):
    return str(v).strip().lower() in ("1", "true", "on", "yes")
