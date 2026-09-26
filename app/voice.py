# -*- coding: utf-8 -*-
"""语音：ASR（语音→文字）与 TTS（文字→语音）。
支持两套后端：openai（OpenAI 兼容音频接口）与 dashscope（阿里云百炼原生）。"""
import requests


class Voice:
    def __init__(self, cfg, llm_api_key):
        self.cfg = cfg
        self.backend = (cfg.get("SPEECH_BACKEND") or "openai").lower()
        self.llm_api_key = llm_api_key or ""
        self.dash_api_key = cfg.get("DASHSCOPE_API_KEY") or ""
        self.llm_base = (cfg.get("LLM_BASE_URL") or "").rstrip("/")
        self.asr_model = cfg.get("ASR_MODEL") or ""
        self.tts_model = cfg.get("TTS_MODEL") or ""
        self.tts_voice = cfg.get("TTS_VOICE") or "nova"

    @property
    def enabled(self):
        return bool(self.asr_model or self.tts_model)

    # ---- ASR ----
    def transcribe(self, audio_bytes, filename="voice.wav"):
        """把音频字节转成文字。"""
        if not self.asr_model or not audio_bytes:
            return ""
        if self.backend == "dashscope":
            return self._asr_dashscope(audio_bytes, filename)
        return self._asr_openai(audio_bytes, filename)

    def _asr_openai(self, audio_bytes, filename):
        url = self.llm_base + "/audio/transcriptions"
        files = {"file": (filename, audio_bytes, "audio/wav")}
        data = {"model": self.asr_model}
        headers = {"Authorization": "Bearer " + self.llm_api_key}
        resp = requests.post(url, headers=headers, files=files, data=data, timeout=60)
        resp.raise_for_status()
        return resp.json().get("text", "").strip()

    def _asr_dashscope(self, audio_bytes, filename):
        # 百炼 paraformer 使用 compatible-mode 的 transcription 接口
        url = "https://dashscope.aliyuncs.com/compatible-mode/v1/audio/transcriptions"
        files = {"file": (filename, audio_bytes, "audio/wav")}
        data = {"model": self.asr_model}
        headers = {"Authorization": "Bearer " + (self.dash_api_key or self.llm_api_key)}
        resp = requests.post(url, headers=headers, files=files, data=data, timeout=60)
        resp.raise_for_status()
        return resp.json().get("text", "").strip()

    # ---- TTS ----
    def synthesize(self, text):
        """把文字转成语音，返回音频字节；失败返回 None。"""
        if not self.tts_model or not text:
            return None
        if self.backend == "dashscope":
            return self._tts_dashscope(text)
        return self._tts_openai(text)

    def synthesize_preview(self, text, voice, model=None):
        """用指定音色/模型合成（用于试听），返回音频字节或 None。"""
        if not self.tts_model or not text or not voice:
            return None
        if self.backend == "dashscope":
            return self._tts_dashscope(text, voice, model)
        old = self.tts_voice
        try:
            self.tts_voice = voice
            return self._tts_openai(text)
        finally:
            self.tts_voice = old

    def _tts_openai(self, text):
        url = self.llm_base + "/audio/speech"
        headers = {"Authorization": "Bearer " + self.llm_api_key,
                   "Content-Type": "application/json"}
        payload = {"model": self.tts_model, "input": text, "voice": self.tts_voice,
                   "response_format": "mp3"}
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.content

    def _tts_dashscope(self, text, voice=None, model=None):
        voice = voice or self.tts_voice
        model = model or self.tts_model or "qwen3-tts-flash"
        dash_key = self.dash_api_key or self.llm_api_key
        headers = {"Authorization": "Bearer " + dash_key, "Content-Type": "application/json"}
        if model.startswith("qwen3-tts"):
            # 旧端点：qwen3-tts-flash 同步返回音频下载地址
            url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
            payload = {"model": model,
                       "input": {"text": text, "voice": voice},
                       "parameters": {"format": "wav", "sample_rate": 24000}}
        else:
            # 新端点：SpeechSynthesizer（cosyvoice-v2/v3、qwen-audio-tts 等）
            url = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
            payload = {"model": model,
                       "input": {"text": text, "voice": voice,
                                 "format": "wav", "sample_rate": 24000}}
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        if resp.status_code != 200:
            return None
        d = resp.json()
        u = (d.get("output") or {}).get("audio", {}).get("url")
        if not u:
            return None
        a = requests.get(u, timeout=60)
        if a.status_code != 200:
            return None
        return a.content
