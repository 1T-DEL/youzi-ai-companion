# -*- coding: utf-8 -*-
"""关系状态引擎：好感度、性格动态演变、相处衰减。
每次用户主动发来一条消息后调用 update()：
  - 根据消息情绪给好感度加减分（封顶 100）
  - 性格特质（熟悉度/温柔度/黏人度/活泼度）随好感度缓慢漂移
  - 长时间不聊天，好感度会缓慢回落（关系会"生疏"一点）
state() 供提示词注入与管理页展示。
"""
import time

TRAIT_DEFAULTS = {
    "熟悉度": 20.0,   # 越聊越熟悉
    "温柔度": 50.0,   # 向好感度收敛：越亲近越温柔
    "黏人度": 35.0,   # 向好感度收敛：越亲近越黏人
    "活泼度": 55.0,   # 随聊天氛围波动
}
AFFECTION_DECAY_PER_DAY = 0.5  # 每天不聊回落多少

# 情绪关键词（轻量启发式，不额外调用大模型）
POSITIVE = ["哈哈", "嘿嘿", "嘻嘻", "开心", "高兴", "喜欢", "想你", "想你了", "爱你",
            "抱抱", "亲亲", "好棒", "真棒", "棒", "幸福", "顺利", "加油", "谢谢",
            "乖", "好耶", "开心死了", "爱死"]
VENTING = ["难过", "伤心", "想哭", "哭了", "哭", "委屈", "累", "好累", "烦", "烦死",
           "焦虑", "压力", "失眠", "睡不着", "难受", "不舒服", "疼", "痛", "郁闷",
           "emo", "孤独", "孤单", "丧", "崩溃", "撑不住", "心情不好"]
NEGATIVE = ["滚", "滚啊", "闭嘴", "讨厌你", "烦死了", "别烦我", "拉黑", "讨厌死",
            "气死我了", "不想理你", "分手吧"]


def _sentiment(text):
    if any(w in text for w in NEGATIVE):
        return "neg"
    if any(w in text for w in POSITIVE):
        return "pos"
    if any(w in text for w in VENTING):
        return "vent"
    return "neu"


def _affection_delta(sent):
    return {"neg": -1.0, "pos": 0.6, "vent": 0.4, "neu": 0.2}[sent]


def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def level_label(score):
    if score >= 80:
        return "热恋期"
    if score >= 60:
        return "亲密期"
    if score >= 40:
        return "熟悉期"
    if score >= 20:
        return "认识期"
    return "初识期"


def level_desc(score):
    """关系阶段叙事（给 persona 注入，让柚子"知道你们走到哪一步了"）。"""
    if score >= 80:
        return "你们已经很亲近很亲近了，像热恋中的恋人：她可以放心撒娇、吃醋、说心里话，会忍不住想你"
    if score >= 60:
        return "你们进入了亲密期：相处自然默契，她开始主动关心你的日常，也会偶尔撒娇、黏你"
    if score >= 40:
        return "你们已经熟悉起来：她记得你的事，说话越来越随意自然，开始有一点专属的默契"
    if score >= 20:
        return "你们还在认识阶段：客气中带着好感，她在慢慢了解你、试探你的喜好"
    return "你们刚认识不久，她还比较拘谨温柔，保持着礼貌的距离"


def state(memory):
    """汇总当前关系状态：好感度 + 性格特质 + 消息数。"""
    aff = memory.get_affection() or {}
    score = float(aff.get("score") or 0)
    traits = memory.all_traits()
    for name, dv in TRAIT_DEFAULTS.items():
        if name not in traits:
            traits[name] = {"value": dv, "updated_ts": 0}
    return {
        "affection": round(score, 1),
        "traits": {n: round(v["value"], 1) for n, v in traits.items()},
        "messages": int(aff.get("messages") or 0),
        "updated_ts": float(aff.get("updated_ts") or 0),
    }


# 触发关系事件的关键词（出现即记录到时间线）
LOVE_WORDS = ["爱你", "喜欢你", "喜欢上你", "表白", "在一起", "当我女朋友", "当我对象", "当我老公", "当我老婆"]
HEALTH_WORDS = ["生病", "感冒", "发烧", "咳嗽", "头疼", "胃疼", "肚子疼", "失眠", "发烧了", "阳了", "不舒服"]
BIRTHDAY_WORDS = ["生日", "纪念日", "周年"]
GOOD_NEWS = ["涨工资", "升职", "中奖", "通过了", "成功了", "拿到offer", "录取", "考试过了", "面试过了"]


def update(memory, user_text):
    """在用户发来一条消息后调用：更新好感度与性格特质，并沉淀关系事件/历史。"""
    now = time.time()
    sent = _sentiment(user_text)

    # 1) 好感度：先做"冷落衰减"，再加本次情绪分
    cur = memory.get_affection() or {}
    score = float(cur.get("score") or 40.0)
    old_score = score
    last_ts = float(cur.get("updated_ts") or now)
    idle_days = max(0.0, (now - last_ts) / 86400.0)
    if idle_days > 1.0:
        score = max(0.0, score - idle_days * AFFECTION_DECAY_PER_DAY)
    score = _clamp(score + _affection_delta(sent))
    msgs = int(cur.get("messages") or 0) + 1
    memory.set_affection(score, messages=msgs, updated_ts=now)
    # 好感度历史曲线：每聊一条都记录一次（供管理页画成长曲线）
    try:
        memory.record_affection(score)
    except Exception:
        pass

    # 2) 性格特质漂移
    familiarity = _clamp(float(memory.get_trait("熟悉度", TRAIT_DEFAULTS["熟悉度"])) + 0.5)
    memory.set_trait("熟悉度", familiarity)

    warmth = memory.get_trait("温柔度", TRAIT_DEFAULTS["温柔度"])
    cling = memory.get_trait("黏人度", TRAIT_DEFAULTS["黏人度"])
    play = memory.get_trait("活泼度", TRAIT_DEFAULTS["活泼度"])
    # 温柔/黏人向好感度收敛（相处久了自然更亲近）
    warmth = _clamp(warmth + (score - warmth) * 0.04)
    cling = _clamp(cling + (score - cling) * 0.03)
    if sent == "pos":
        play = _clamp(play + 0.3)
    elif sent == "vent":
        play = _clamp(play - 0.15)  # 你低落时她更安静体贴
    else:
        play = _clamp(play + 0.05)
    memory.set_trait("温柔度", warmth)
    memory.set_trait("黏人度", cling)
    memory.set_trait("活泼度", play)

    # 3) 关系事件时间线（里程碑/情绪事件，只在首次出现时记录）
    try:
        # 等级跨越
        old_lv = level_label(old_score)
        new_lv = level_label(score)
        if old_lv != new_lv and score > old_score:
            memory.add_event("level", "你们进入了「{0}」".format(new_lv))
        # 第一次说爱/表白
        if any(w in user_text for w in LOVE_WORDS) and not memory.has_event_type("love"):
            memory.add_event("love", "对方第一次对你表达了喜欢/爱意")
        # 身体不适
        if any(w in user_text for w in HEALTH_WORDS) and not memory.has_event_type("health"):
            memory.add_event("health", "对方身体不舒服，你好好记着要多多关心")
        # 重要日子
        if any(w in user_text for w in BIRTHDAY_WORDS) and not memory.has_event_type("bigday"):
            memory.add_event("bigday", "对方提到了生日/纪念日这类重要的日子")
        # 好消息
        if any(w in user_text for w in GOOD_NEWS) and not memory.has_event_type("goodnews"):
            memory.add_event("goodnews", "对方遇到了一件值得开心的事")
    except Exception:
        pass
    return state(memory)
