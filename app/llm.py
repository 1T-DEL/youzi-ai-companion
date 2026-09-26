# -*- coding: utf-8 -*-
"""大模型客户端：OpenAI 兼容 Chat Completions。"""
import requests


class LLM:
    def __init__(self, base_url, api_key, model, temperature, max_tokens, timeout):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.temperature = float(temperature or 0.75)
        self.max_tokens = int(max_tokens or 500)
        self.timeout = int(timeout or 45)

    @property
    def configured(self):
        return bool(self.base_url and self.api_key and self.model)

    def chat(self, messages, max_tokens=None):
        """调用 Chat Completions，返回助手文本。失败抛出异常。"""
        url = self.base_url + "/chat/completions"
        headers = {"Authorization": "Bearer " + self.api_key,
                   "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "stream": False,
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
