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
        # GPT-SoVITS 本地后端（可选）：自定义音色克隆
        self.gpt_sovits_url = (cfg.get("GPT_SOVITS_URL") or "http://127.0.0.1:9880").rstrip("/")
        self.gpt_sovits_refer = cfg.get("GPT_SOVITS_REFER_WAV") or ""
        self.gpt_sovits_prompt = cfg.get("GPT_SOVITS_PROMPT_TEXT") or ""
        self.gpt_sovits_emotion = cfg.get("GPT_SOVITS_EMOTION") or ""

    @property
    def enabled(self):
        if self.backend == "gpstsvits":
            return True  # 本地音色克隆不依赖云 TTS 配置
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
    def synthesize(self, text, emotion=None):
        """把文字转成语音，返回音频字节；失败返回 None。

        emotion：可选情感（happy/sad/angry/fearful/disgusted/surprised/neutral），
        dashscope 后端支持；gpstsvits 后端通过参考音色/情感词生效；openai 后端忽略。
        """
        if not text:
            return None
        if self.backend == "gpstsvits":
            return self._tts_gpstsvits(text, emotion=emotion)
        if not self.tts_model:
            return None
        if self.backend == "dashscope":
            return self._tts_dashscope(text, emotion=emotion)
        return self._tts_openai(text)

    # ---- TTS 意群分段（小凌式：整句一次性生成听感死板；按标点拆又逐字断裂） ----
    @staticmethod
    def split_semantic_units(text, max_len=26):
        """按语义意群切分，拒绝机械标点断裂；单句最多拆两段。

        - 先按句界（。！？…；\n）分句
        - 长句在逗号/顿号处按"最长停顿"拆为两段（一句最多 2 段）
        - 过短的碎片并入相邻段，避免"逐字断裂"
        """
        import re
        text = (text or "").strip()
        if not text:
            return []
        # 1) 分句
        sentences = [s.strip() for s in re.split(r"[。！？…\n]+", text) if s.strip()]
        if not sentences:
            sentences = [text]
        # 2) 长句按意群拆（每句最多两段）
        units = []
        for s in sentences:
            if len(s) <= max_len:
                units.append(s)
                continue
            # 找所有可停顿位置（，、：；——及空格），选最接近中点的那个切
            stops = [m.start() for m in re.finditer(r"[，、：；—\s]", s)]
            if not stops:
                units.append(s)
                continue
            mid = len(s) // 2
            cut = min(stops, key=lambda p: abs(p - mid))
            if cut < 2 or cut > len(s) - 3:
                units.append(s)
                continue
            units.append(s[:cut].rstrip("，、：；— ").strip())
            units.append(s[cut:].lstrip("，、：；— ").strip())
        # 3) 合并碎片：太短的段并入前一段（避免逐字断裂感）
        merged = []
        for u in units:
            if merged and len(u) <= 4:
                merged[-1] = merged[-1] + u
            else:
                merged.append(u)
        return merged

    def synthesize_sequence(self, text, emotion=None):
        """意群分段合成：返回 [ {"text": 段文本, "ok": bool, "audio": bytes|None} ]。
        前端可"播第一段的同时预取下一段"，形成自然节奏。
        """
        segs = self.split_semantic_units(text)
        out = []
        for seg in segs:
            audio = self.synthesize(seg, emotion=emotion)
            out.append({"text": seg, "ok": audio is not None, "audio": audio})
        return out

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

    def _tts_gpstsvits(self, text, emotion=None):
        """调用本地 GPT-SoVITS（音色克隆 TTS）合成，返回 wav 字节或 None。

        要求：本机/局域网已跑 GPT-SoVITS 的 api_v2（默认端口 9880）。
        配置 .env：
          SPEECH_BACKEND=gpstsvits
          GPT_SOVITS_URL=http://127.0.0.1:9880
          GPT_SOVITS_REFER_WAV=E:/models/your_voice.wav   （可选：参考音色）
          GPT_SOVITS_PROMPT_TEXT=参考音频里的原文            （可选）
        emotion 生效方式：在文本前缀加情感提示词（如 [happy] / [sad]），
        可被 GPT-SoVITS 的 prompt 引导风格（效果取决于模型版本）。
        """
        import requests as _rq
        try:
            payload = {"text": text, "text_language": "zh"}
            if self.gpt_sovits_refer:
                payload["refer_wav_path"] = self.gpt_sovits_refer
                payload["prompt_text"] = self.gpt_sovits_prompt
                payload["prompt_language"] = "zh"
            if emotion and self.gpt_sovits_emotion:
                payload["prompt_text"] = self.gpt_sovits_emotion
            resp = _rq.post(self.gpt_sovits_url + "/tts", json=payload, timeout=120)
            if resp.status_code != 200:
                return None
            if len(resp.content) < 100:
                return None
            return resp.content
        except Exception:
            return None

    def _tts_openai(self, text):
        url = self.llm_base + "/audio/speech"
        headers = {"Authorization": "Bearer " + self.llm_api_key,
                   "Content-Type": "application/json"}
        payload = {"model": self.tts_model, "input": text, "voice": self.tts_voice,
                   "response_format": "mp3"}
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.content

    def _tts_dashscope(self, text, voice=None, model=None, emotion=None):
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
        if emotion:
            payload["input"]["emotion"] = emotion
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
