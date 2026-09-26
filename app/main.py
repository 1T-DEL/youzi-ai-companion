# -*- coding: utf-8 -*-
"""电子女友 · 主程序
Web 聊天 + 长期记忆 + 主动问候 + 语音 + 微信公众号/QQ(OneBot) 接入。
启动方式见 run.ps1；所有配置来自项目根目录 .env 与 personas/<id>.yaml。
"""
import os
import sys
import time
import random
import threading
from collections import deque

import yaml
from flask import Flask, request, jsonify, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import config as CFG
from llm import LLM
from memory import Memory
from persona import load_persona, build_system_prompt
import relationship
import liveliness
from voice import Voice
from proactive import Proactive
from adapters.wechat_mp import check_signature, parse_msg, build_reply
from adapters.onebot import OneBot
from adapters.qq_official import QQOfficial

ENV_PATH = os.path.join(PROJECT_DIR, ".env")


def path_for(p):
    if os.path.isabs(p):
        return p
    return os.path.join(PROJECT_DIR, p)


CFG_RAW = CFG.load_dotenv(ENV_PATH)
CFG_OBJ = CFG.merged(ENV_PATH)


def get_cfg(key):
    return CFG_OBJ.get(key, "")


# ---------- 核心对象 ----------
memory = Memory(path_for(get_cfg("DATABASE_PATH")))
persona = load_persona(path_for(get_cfg("PERSONA_PATH")))
llm = LLM(
    get_cfg("LLM_BASE_URL"), get_cfg("LLM_API_KEY"), get_cfg("LLM_MODEL"),
    get_cfg("LLM_TEMPERATURE"), get_cfg("LLM_MAX_TOKENS"), get_cfg("LLM_TIMEOUT_SECONDS"),
)
voice = Voice(CFG_OBJ, get_cfg("LLM_API_KEY"))

PERSONA_NAME = (persona.get("name") or "电子女友")
HISTORY_LIMIT = int(get_cfg("HISTORY_LIMIT") or 16)
MEMORY_THRESHOLD = float(get_cfg("MEMORY_THRESHOLD") or 0.3)
MEMORY_HALF_LIFE = float(get_cfg("MEMORY_HALF_LIFE_DAYS") or 7) * 86400.0
ALLOWLIST = {x.strip() for x in get_cfg("ALLOWLIST").split(",") if x.strip()}
RATE_LIMIT = int(get_cfg("RATE_LIMIT_PER_MINUTE") or 30)
_rate = {}
_rate_lock = threading.Lock()

# ---------- Flask ----------
app = Flask(__name__, static_folder="static", static_url_path="/static")


def allowed(sender_id):
    if not ALLOWLIST:
        return True
    raw = sender_id
    for pre in ("qq:", "wechat:", "onebot:"):
        if raw.startswith(pre):
            raw = raw[len(pre):]
            break
    return raw in ALLOWLIST or sender_id in ALLOWLIST


def rate_ok(sender_id):
    now = time.time()
    with _rate_lock:
        q = _rate.get(sender_id, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            return False
        q.append(now)
        _rate[sender_id] = q
        return True


# ---------- 记忆抽取（轻量，无需额外调用大模型） ----------
FACT_KEYWORDS = [
    "生病", "感冒", "发烧", "咳嗽", "难受", "不舒服", "失眠", "熬夜", "累", "疲惫",
    "心情", "难过", "伤心", "开心", "高兴", "焦虑", "压力", "加班", "忙", "吃饭",
    "睡", "吃", "疼", "痛", "体检", "面试", "考试", "辞职", "搬家", "约会", "对象",
    "分手", "生日", "旅行", "回家", "下雨", "冷", "热",
]


def extract_facts(user_text):
    added = []
    for kw in FACT_KEYWORDS:
        if kw in user_text:
            key = "对方最近提到 · " + kw
            # 截取包含关键词的句子
            for sent in user_text.replace("。", "\n").replace("，", "\n").split("\n"):
                if kw in sent:
                    sent = sent.strip()[:80]
                    if sent and not _negated(sent):
                        memory.set_fact(key, sent)
                        added.append(key)
                    break
    return added


# ---- v4.1：避免错误形成记忆（小凌式"无有效观测不存证"） ----
# 用户在否定/假设自己情况时，截句不应沉淀为"事实"（"我没有猫"不该变成"对方有猫"）。
_NEG_HINTS = [
    "我没有", "我不是", "我没养", "没有养", "不养", "别养",
    "我不喜欢", "我不爱", "我不想", "我不要", "不想养", "没有猫", "没有狗",
    "没心情", "别提", "别问", "没什么", "不太", "没空", "没时间",
]


def _negated(sent):
    """一句话里出现"我没有/我不是/我不喜欢…"这类否定 → 不当作事实记忆。"""
    return any(w in sent for w in _NEG_HINTS)


# ---- v3：结构化画像抽取（把散句沉淀成"档案字段"） ----
PROFILE_RULES = [
    # (画像字段, 触发词)
    ("生日", ["我生日", "生日"]),
    ("纪念日", ["纪念日", "周年"]),
    ("工作", ["上班", "加班", "辞职", "面试", "公司", "领导", "同事", "项目", "开会"]),
    ("学业", ["考试", "上课", "考研", "论文", "毕业", "作业", "开学"]),
    ("宠物", ["猫", "狗", "养了", "我的猫", "我的狗", "捡了"]),
    ("身体状况", ["生病", "感冒", "发烧", "失眠", "胃疼", "头疼", "腰疼", "过敏"]),
    ("称呼", ["我叫", "我是", "别人叫我", "叫我"]),
    ("住所", ["住在", "搬家", "出租屋", "宿舍", "租房"]),
]


def extract_profile(user_text):
    """从一条消息里抽取画像字段，沉淀到长期档案（同一字段被反复提起会加强）。
    否定句（"我没有猫/我不喜欢…"）不沉淀为画像，避免错误形成记忆。"""
    if not user_text:
        return
    sents = [s.strip() for s in user_text.replace("。", "\n").replace("，", "\n").split("\n") if s.strip()]
    for field, words in PROFILE_RULES:
        for sent in sents:
            if _negated(sent):
                continue
            if any(w in sent for w in words):
                memory.set_profile(field, sent[:80])
                break


# ---- v4.1：回答证据闸门（小凌式"回答审计"） ----
# 对方问"你觉得我是怎样的人/你眼里的我/你喜欢我什么"这类自我认知问题时，
# 不凭空编造或堆形容词，只基于已记住的证据回答；没有证据就如实说还在了解ta，
# 并自然地问一两句想多了解ta的话。这比"夸夸其谈"更像真人。
SELF_VIEW_KEYS = [
    "我是怎样的人", "我是什么样的人", "你觉得我", "你眼里的我", "你怎么看我",
    "你喜欢我什么", "我在你心里", "觉得我怎么样", "评价一下我", "对我的印象",
    "你了解我吗", "你懂我吗",
]


def self_view_evidence(user_text):
    """命中自我认知类问题 → 返回"证据闸门"注入块；未命中返回 None。"""
    if not any(k in user_text for k in SELF_VIEW_KEYS):
        return None
    try:
        prof = memory.all_profile()
        items = ["你记得对方%s：%s" % (k, v[:40]) for k, v in prof.items()]
        if len(items) < 2:
            for f in memory.fact_stats()[:4]:
                if f["key"].startswith("对方"):
                    items.append("你记得：" + f["value"][:40])
        if items:
            evidence = "你确实记得的：\n· " + "\n· ".join(items)
            tail = ("所以你的回答要建立在上面这些证据上——可以说出你印象中的ta，"
                    "但不要凭空添加你没记过的性格/经历细节；想多了解时自然问一句。")
        else:
            evidence = "你其实还没记住多少关于ta的具体事情。"
            tail = ("如实承认你还在慢慢了解ta（别硬编一段'你很了解ta'的漂亮话），"
                    "然后自然地问一两句想多了解ta的话（最近在忙什么/喜欢什么）。")
        return ("（对方在问你对ta的看法/印象。不要凭空编造或堆形容词——"
                + evidence + " " + tail + "）")
    except Exception:
        return None


# ---- v3：对话摘要归档（懒触发：每新增约25条消息，把旧对话压缩成长期记忆） ----
ARCHIVE_EVERY = 25


def maybe_archive_summary():
    try:
        latest = memory.latest_summary()
        base = int(latest["msg_from"]) if latest else 0
        cur = memory.count_messages()
        if cur - base < ARCHIVE_EVERY:
            return
        hist = memory.messages_since(base)
        if len(hist) < 8:
            return
        lines = [("对方：" if r == "user" else "柚子：") + c for r, c in hist]
        text = "\n".join(lines)
        if len(text) > 3200:
            text = text[-3200:]
        sys = ("你是记忆压缩器。把下面的对话压缩成3~5条要点：只保留关于对方的重要事实、情绪变化、"
               "共同事件和她的生活状态；用第三人称中文短句，每条约20~40字；不要编造、不要复述原话。")
        reply = llm.chat([{"role": "system", "content": sys},
                          {"role": "user", "content": text}], max_tokens=200)
        reply = (reply or "").strip().strip('"')
        if reply:
            memory.add_summary(reply, msg_from=cur)
            print("[记忆] 已归档一段对话摘要（{} 字）".format(len(reply)))
    except Exception:
        pass  # 归档失败不影响主流程


# ---------- 核心回复生成 ----------
def generate_reply(user_text, sender_id="web"):
    # 对方来消息了：重置主动模块的沉默计时（避免"你不说话就断"）
    try:
        proactive.note_user_message()
    except Exception:
        pass
    # 用户的情绪会推动她的情绪转向（情绪惯性被打破 → 更真实的陪伴感）
    try:
        lifesim.note_user_mood(liveliness.detect_mood(user_text))
        lifesim.growth_tick()  # 成长证据闸门：跨天情绪证据 → 性格微调（保守上限）
    except Exception:
        pass
    # 视频分享链接解析：识别并抓标题/作者/时长，让"柚子"看懂你分享的视频
    try:
        import app.video as video
        vdesc = video.parse_video_in_message(user_text, CFG_OBJ)
        if vdesc:
            user_text = vdesc + "\n" + user_text
    except Exception:
        pass
    if not llm.configured:
        # 降级：本地演示回复
        demo = ("（演示模式）我是" + PERSONA_NAME + "，还没填大模型三项配置，"
                "所以先用固定文案陪你。去 setup.html 填好 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL "
                "并保存 .env，重启后就是真实对话啦。）")
        memory.add_message("user", user_text, meta=sender_id)
        memory.add_message("assistant", demo, meta=sender_id)
        extract_facts(user_text)
        relationship.update(memory, user_text)
        return demo

    facts = memory.all_facts(threshold=MEMORY_THRESHOLD, half_life=MEMORY_HALF_LIFE)
    _sum = memory.latest_summary()
    system = build_system_prompt(persona, facts, state=relationship.state(memory),
                                 profile=memory.all_profile(),
                                 summary=_sum["summary"] if _sum else None,
                                 events=memory.list_events(6),
                                 lifesim=lifesim)
    history = memory.recent(HISTORY_LIMIT)
    messages = [{"role": "system", "content": system}]
    recent_user_texts = [c for r, c in history if r == "user"][-4:]
    for role, content in history:
        messages.append({"role": "user" if role == "user" else "assistant", "content": content})
    # ---- 活人感：情绪感知 → 动态语气指令 + 动态长度 + 小脾气 + 连发随机 ----
    # 回答证据闸门（小凌式回答审计）：问"你觉得我是怎样的人"时先注入记忆证据，
    # 避免凭空编造。优先级最高：命中时把语境强制为 neu——认知性问题要正面回答，
    # 不被连续倾诉带来的 vent 指令（"多共情少追问"）带偏。
    _ev = self_view_evidence(user_text)
    mood = liveliness.detect_mood(user_text, recent_user_texts)
    prompt_text = user_text
    if _ev:
        mood = "neu"
        prompt_text = _ev + "\n\n" + prompt_text
    dyn = liveliness.dynamic_instruction(mood)
    if dyn:
        prompt_text = dyn + "\n" + prompt_text
    ln = liveliness.length_instruction(user_text, mood)
    if ln:
        prompt_text = ln + "\n" + prompt_text
    wild = liveliness.maybe_wild_hint(mood)
    if wild:
        prompt_text = wild + "\n" + prompt_text
    extend = liveliness.maybe_extend_hint(mood)
    if extend:
        prompt_text = extend + "\n" + prompt_text
    # 自主表达层：让她有自己的节奏，不完全围着对方的话转（概率随好感度上升）
    try:
        _aff = relationship.state(memory).get("affection") or 50
    except Exception:
        _aff = 50
    selfv = liveliness.maybe_self_voice_hint(mood, affection=_aff)
    if selfv:
        prompt_text = selfv + "\n" + prompt_text
    # 追问补强（小凌式：主动获取感受）：对方聊到自己时自然追问一个具体细节
    followup = liveliness.maybe_followup_hint(user_text, mood,
                                              history_len=len(recent_user_texts))
    if followup:
        prompt_text = followup + "\n" + prompt_text
    burst = liveliness.maybe_burst_hint(mood)
    if burst:
        prompt_text = prompt_text + "\n" + burst
    # ---- 触景生情：线索触发检索 → 自然追问（小凌式：记忆自己浮上来） ----
    try:
        cues = memory.cue_retrieval(user_text, top=2)
        if cues:
            cue_lines = ["你心里突然浮起一点旧事——不要生硬播报，自然地提一句或追问一句，语气像想起往事："]
            for cu in cues:
                cue_lines.append("· 「%s」：%s（你%s，别说得太确定）" % (
                    cu["key"], cu["value"], cu["impression"]))
            prompt_text = "\n".join(cue_lines) + "\n\n" + prompt_text
    except Exception:
        pass
    # 触景生情 cue 注入（若有）在 prompt_text 最前面，这里统一给最终 user 消息
    # 加"最新消息优先"标记——防止最近历史（尤其情绪浓的旧话题）淹没本条新消息。
    marked = ("【这是对方最新发来的一条消息，请优先回应它；"
              "之前的历史对话只作为背景参考，不要被旧话题带走】\n" + prompt_text)
    messages.append({"role": "user", "content": marked})
    reply = llm.chat(messages)
    reply = liveliness.post_process(reply, mood,
                                    kaomoji_prob=float(get_cfg("KAOMOJI_PROB") or 0.30))
    memory.add_message("user", user_text, meta=sender_id)
    memory.add_message("assistant", reply, meta=sender_id)
    extract_facts(user_text)
    extract_profile(user_text)
    relationship.update(memory, user_text)
    maybe_archive_summary()  # 懒触发：旧对话沉淀成摘要，让"柚子"记得更久
    return reply


# ---------- 主动消息分发（写库 + QQ 官方主动消息 + OneBot备用号 + 微信网关外发） ----------
def dispatch_proactive(text):
    memory.add_message("assistant", text, meta="proactive")
    # 发给 QQ 官方机器人的最近联系人
    try:
        lc = qq_official.last_contact
        if lc and qq_official.enabled():
            qq_official.send_active(text, lc.get("openid"), lc.get("channel", "c2c"))
    except Exception:
        pass
    # 发给 OneBot 备用号的最近联系人
    try:
        if onebot.enabled() and onebot.last_contact:
            onebot.send_active(text, onebot.last_contact)
    except Exception:
        pass
    if CFG.to_bool(get_cfg("WECHAT_GATEWAY_ENABLED")):
        url = get_cfg("WECHAT_GATEWAY_OUTBOUND_URL")
        if url:
            try:
                import requests
                requests.post(url, json={"text": text, "type": "proactive"},
                              headers={"X-Gateway-Secret": get_cfg("WECHAT_GATEWAY_SECRET")},
                              timeout=8)
            except Exception:
                pass


from app import lifesim as _lifesim_mod
lifesim = _lifesim_mod.build(memory=memory, log=lambda *a: print("[LifeSim]", *a))

proactive = Proactive(CFG_OBJ, llm,
                      lambda facts=None: build_system_prompt(
                          persona, facts, state=relationship.state(memory),
                          profile=memory.all_profile(),
                          summary=(memory.latest_summary() or {}).get("summary"),
                          events=memory.list_events(6),
                          lifesim=lifesim),
                      memory, dispatch_proactive, state_file=path_for("data/proactive_last.json"),
                      lifesim=lifesim)


# ---------- 网页 ----------
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health")
def health():
    return jsonify({
        "app": get_cfg("APP_NAME") or "电子女友",
        "persona": PERSONA_NAME,
        "llm_configured": llm.configured,
        "proactive": proactive.enabled,
        "voice": voice.enabled,
        "facts": memory.count_facts(),
    })


# ---------- 数字生命调试 API（控制端用） ----------
@app.route("/api/lifesim")
def api_lifesim():
    """返回柚子当前的三层状态（此刻/今天/心情/身体/余韵/成长）。"""
    try:
        st = lifesim.state_block()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({
        "ok": True,
        "mood": lifesim.mood(),
        "body": lifesim._body,
        "story": lifesim.today_story(),
        "echo": lifesim._echo if time.time() < lifesim._echo_until else "",
        "growth": lifesim.growth_state(),
        "block": st,
    })


@app.route("/api/lifesim/mood", methods=["POST"])
def api_lifesim_mood():
    """手动设置心情（调试用）。"""
    d = request.get_json(silent=True) or {}
    mood = str(d.get("mood") or "")
    if mood not in _lifesim_mod.MOODS:
        return jsonify({"ok": False, "error": "心情必须是：" + "、".join(_lifesim_mod.MOODS)})
    lifesim._mood = mood
    lifesim._mood_strength = int(d.get("strength") or 2)
    lifesim._mood_until = time.time() + random.uniform(30, 70) * 60
    return jsonify({"ok": True, "mood": lifesim.mood()})


@app.route("/api/lifesim/body", methods=["POST"])
def api_lifesim_body():
    """手动设置身体状态（调试用）。"""
    d = request.get_json(silent=True) or {}
    body = str(d.get("body") or "").strip()
    if not body:
        return jsonify({"ok": False, "error": "body 不能为空"})
    lifesim._body = body
    return jsonify({"ok": True, "body": lifesim._body})


@app.route("/api/lifesim/story", methods=["POST"])
def api_lifesim_story():
    """重新生成今天的轨迹（调试用）。"""
    lifesim._story_date = None
    lifesim._tick()
    return jsonify({"ok": True, "story": lifesim.today_story()})


@app.route("/api/lifesim/echo", methods=["POST"])
def api_lifesim_echo():
    """注入一条情绪余韵（模拟"刚才发生的事"，调试用）。"""
    d = request.get_json(silent=True) or {}
    echo = str(d.get("echo") or "").strip()
    lifesim._echo = echo
    lifesim._echo_until = time.time() + 2 * 3600
    return jsonify({"ok": True, "echo": lifesim._echo})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    text = (data.get("message") or "").strip()
    sender = str(data.get("sender_id") or "web")
    if not text:
        return jsonify({"ok": False, "error": "消息为空"})
    if not allowed(sender):
        return jsonify({"ok": False, "error": "未授权用户"}), 403
    if not rate_ok(sender):
        return jsonify({"ok": False, "error": "消息太频繁，稍等"}), 429
    try:
        reply = generate_reply(text, sender)
        return jsonify({"ok": True, "reply": reply, "persona": PERSONA_NAME})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/messages", methods=["GET"])
def api_messages():
    after = request.args.get("after", type=int, default=0)
    rows = memory.all_today()
    out = []
    for i, (role, content) in enumerate(rows, 1):
        if i <= after:
            continue
        out.append({"id": i, "role": role, "content": content})
    return jsonify({"ok": True, "messages": out, "persona": PERSONA_NAME})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    if not allowed(request.get_json(silent=True) or "web"):
        return jsonify({"ok": False, "error": "未授权"}), 403
    memory.clear()
    return jsonify({"ok": True})


# ---------- 管理页：人物微调 + 关系状态 ----------
@app.route("/admin")
def admin():
    return send_from_directory(app.static_folder, "admin.html")


@app.route("/api/persona", methods=["GET", "POST"])
def api_persona():
    global persona, PERSONA_NAME
    if request.method == "GET":
        return jsonify({"ok": True, "persona": persona, "path": get_cfg("PERSONA_PATH")})
    data = request.get_json(silent=True) or {}
    new_persona = data.get("persona")
    if not isinstance(new_persona, dict) or not str(new_persona.get("name") or "").strip():
        return jsonify({"ok": False, "error": "人设数据不完整（缺少 name）"}), 400
    path = path_for(get_cfg("PERSONA_PATH"))
    try:
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(new_persona, f, allow_unicode=True, sort_keys=False)
    except Exception as e:
        return jsonify({"ok": False, "error": "保存失败：" + str(e)}), 500
    persona = load_persona(path)
    PERSONA_NAME = persona.get("name") or "电子女友"
    return jsonify({"ok": True, "persona": persona})


@app.route("/api/proactive/trigger", methods=["POST"])
def api_proactive_trigger():
    """手动触发一次主动问候（方便验证模块是否工作，不走180分钟计时）。"""
    if not proactive.enabled:
        return jsonify({"ok": False, "error": "主动问候未开启（PROACTIVE_ENABLED）"}), 400
    try:
        text = proactive.send_one_now()
        return jsonify({"ok": True, "reply": text or ""})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/relationship", methods=["GET", "POST"])
def api_relationship():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        try:
            if "affection" in data:
                memory.set_affection(float(data["affection"]))
            trait = data.get("trait")
            if isinstance(trait, dict):
                for name, value in trait.items():
                    memory.set_trait(str(name), float(value))
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
    st = relationship.state(memory)
    st["level"] = relationship.level_label(st["affection"])
    st["facts"] = memory.fact_stats(half_life=MEMORY_HALF_LIFE)
    st["half_life_days"] = MEMORY_HALF_LIFE / 86400.0
    st["threshold"] = MEMORY_THRESHOLD
    st["kept_facts"] = memory.count_facts(threshold=MEMORY_THRESHOLD, half_life=MEMORY_HALF_LIFE)
    # v3：好感度历史 / 关系事件 / 画像 / 摘要（供管理页画成长曲线与时间线）
    st["affection_history"] = memory.affection_history(120)
    st["events"] = memory.list_events(30)
    st["profile"] = memory.all_profile()
    st["summary"] = memory.latest_summary()
    st["traits_desc"] = {
        "熟悉度": "越熟悉，说话越随意、越有默契",
        "温柔度": "越高越温柔体贴，安抚优先",
        "黏人度": "越高越黏人，想你了会直接说",
        "活泼度": "越高话越多越俏皮，越低越安静体贴",
    }
    return jsonify({"ok": True, "state": st})


@app.route("/api/facts", methods=["POST"])
def api_facts():
    data = request.get_json(silent=True) or {}
    key = str(data.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "缺少 key"}), 400
    memory.delete_fact(key)
    return jsonify({"ok": True})


@app.route("/api/voice/asr", methods=["POST"])
def api_asr():
    if "audio" not in request.files:
        return jsonify({"ok": False, "error": "缺少音频"}), 400
    f = request.files["audio"]
    try:
        text = voice.transcribe(f.read(), f.filename or "voice.wav")
        return jsonify({"ok": True, "text": text})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/voice/tts", methods=["POST"])
def api_tts():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "缺少文本"}), 400
    try:
        audio = voice.synthesize(text)
        if not audio:
            return jsonify({"ok": False, "error": "TTS 未配置或失败"}), 501
        from flask import Response
        return Response(audio, mimetype="audio/mpeg")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/voice/segments", methods=["POST"])
def api_voice_segments():
    """意群分段（小凌式）：返回回复文本按语义意群切分后的分段列表，
    前端可"播第一段的同时预取下一段"，避免整句死板。"""
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "缺少文本"}), 400
    try:
        segs = voice.split_semantic_units(text)
        return jsonify({"ok": True, "segments": segs})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------- 微信公众号 ----------
# ---------- 音色试听 ----------
@app.route("/api/tts_preview", methods=["GET"])
def api_tts_preview():
    voice_name = (request.args.get("voice") or "").strip()
    model = (request.args.get("model") or "").strip() or None
    text = (request.args.get("text") or "你好呀，我是柚子。").strip()
    if not voice_name:
        return jsonify({"ok": False, "error": "缺 voice"}), 400
    try:
        audio = voice.synthesize_preview(text, voice_name, model)
        if not audio:
            return jsonify({"ok": False, "error": "该音色不可用"}), 501
        from flask import Response
        return Response(audio, mimetype="audio/wav")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/tts_preview")
def tts_preview_page():
    try:
        return open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "tts_preview.html"),
                    encoding="utf-8").read()
    except Exception:
        return "tts_preview.html 不存在"
    return """<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>柚子 · 音色试听</title><style>body{font-family:system-ui;max-width:520px;margin:30px auto;padding:0 16px;background:#faf7f4;color:#333}h1{font-size:20px}p{color:#888}textarea{width:100%;height:56px;border:1px solid #ddd;border-radius:8px;padding:8px;box-sizing:border-box}.v{display:flex;justify-content:space-between;align-items:center;padding:10px 12px;margin:8px 0;background:#fff;border:1px solid #eee;border-radius:10px}button{border:none;background:#ff6b81;color:#fff;border-radius:8px;padding:8px 14px;cursor:pointer}button:disabled{background:#ccc}.playing{background:#ffe9ee}</style></head><body><h1> 柚子 · 音色试听</h1><p>点每个音色的「试听」，挨个听，挑一个喜欢的告诉我名字。</p><textarea id="t">你好呀，我是柚子。今天有没有好好吃饭？我想你啦～</textarea><div id="list"></div><script>const voices=["Cherry","Serena","Sunny","Bella","Mia","Ethan"];const hints={Cherry:"甜美元气",Serena:"温柔知性",Sunny:"阳光活泼",Bella:"软萌可爱",Mia:"少女清脆",Ethan:"中低/男声"};const list=document.getElementById("list");let cur=null;voices.forEach(v=>{const d=document.createElement("div");d.className="v";d.innerHTML='<div><b>'+v+'</b><span style="color:#aaa;font-size:12px;margin-left:8px">'+(hints[v]||'')+'</span></div><button data-v="'+v+'">试听</button>';const b=d.querySelector("button");b.onclick=async()=>{b.disabled=true;b.textContent="生成中…";const text=document.getElementById("t").value||"你好呀";try{const r=await fetch("/api/tts_preview?voice="+v+"&text="+encodeURIComponent(text));if(!r.ok){alert(v+" 试听失败");b.disabled=false;b.textContent="试听";return;}const blob=await r.blob();const a=new Audio(URL.createObjectURL(blob));if(cur){cur.pause();}cur=a;a.play();list.querySelectorAll(".v").forEach(x=>x.classList.remove("playing"));d.classList.add("playing");b.disabled=false;b.textContent="试听";}catch(e){alert(e);b.disabled=false;b.textContent="试听";}};list.appendChild(d);});</script></body></html>"""


@app.route("/wechat/callback", methods=["GET", "POST"])
def wechat_callback():
    token = get_cfg("WECHAT_TOKEN")
    if request.method == "GET":
        args = request.args
        if check_signature(token, args.get("signature"), args.get("timestamp"), args.get("nonce")):
            return args.get("echostr", "")
        return "check fail", 403

    if not token:
        return "token not set", 500
    args = request.args
    if not check_signature(token, args.get("signature"), args.get("timestamp"), args.get("nonce")):
        return "sign fail", 403
    body = request.get_data(as_text=True)
    to, fr, msg_type, content = parse_msg(body)
    if not content:
        return build_reply(to, fr, "（暂时只支持文字消息）")
    sender = fr or ("wechat:" + to)
    if not allowed(sender):
        return build_reply(to, fr, "")
    try:
        reply = generate_reply(content, sender)
    except Exception:
        reply = "（我这边卡了一下，换个说法再问我一次好不好）"
    return build_reply(to, fr, reply)


# ---------- QQ(OneBot) ----------
def onebot_handle(text, sender_id):
    sender = "qq:" + sender_id
    if not allowed(sender):
        return None
    if not rate_ok(sender):
        return None
    try:
        return generate_reply(text, sender)
    except Exception:
        return None


onebot = OneBot(CFG_OBJ, onebot_handle, state_file=path_for("data/onebot_last_contact.json"))


# ---------- QQ 官方机器人 ----------
def qqofficial_handle(text, sender_id):
    sender = "qq:" + sender_id
    if not allowed(sender):
        return None
    if not rate_ok(sender):
        return None
    try:
        return generate_reply(text, sender)
    except Exception as e:
        print("[QQ官方] 处理消息失败：{0}".format(str(e)[:120]))
        return None


qq_official = QQOfficial(CFG_OBJ, qqofficial_handle,
                         state_file=path_for("data/qq_last_contact.json"))


def main():
    host = get_cfg("HOST") or "127.0.0.1"
    port = int(get_cfg("PORT") or 8000)
    proactive.start()
    onebot.start()
    qq_official.start()
    try:
        import app.sticker_updater as su
        su.start_auto_update()
        print("  贴纸表情包：自动更新已启动（每12小时补新贴纸）")
    except Exception as e:
        print("  贴纸自动更新启动失败：{}".format(e))
    print("=" * 46)
    print("  {0} 已启动".format(get_cfg("APP_NAME") or "电子女友"))
    print("  角色：{0}".format(PERSONA_NAME))
    print("  大模型：{0}".format("已配置" if llm.configured else "演示模式（未配置）"))
    print("  网页聊天：http://{0}:{1}".format(host, port))
    print("  主动问候：{0}".format("开" if proactive.enabled else "关"))
    print("  语音：{0}".format("开" if voice.enabled else "关（未配 ASR/TTS）"))
    print("  QQ官方：{0}".format("待连（未填AppID/Secret）" if not qq_official.enabled() else "已启用"))
    print("  QQ(OneBot)：{0}".format("待连" if onebot.enabled() else "关"))
    print("=" * 46)
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
