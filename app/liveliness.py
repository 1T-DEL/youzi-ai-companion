# -*- coding: utf-8 -*-
"""活人感引擎：情绪感知 + 动态语气指令 + 颜文字 + 连发随机化。

让回复更像真人微信：
 1. 先判断对方这条消息的语境/情绪（vent / pos / neg / casual / neu）；
 2. 动态生成追加给大模型的语气指令（普通闲聊不腻、低落多哄、开心一起开心）；
 3. 按概率提示"连发 2~3 条短消息"（用换行分隔），让文本天生多行；
 4. 后处理偶尔补一个颜文字收尾（不重复、不在负面语境硬加）。
"""

import random

# ---- 情绪/语境关键词（轻量启发式，不额外调用大模型） ----
VENTING = [
    "难过", "伤心", "想哭", "哭了", "哭", "委屈", "累", "好累", "烦", "烦死",
    "焦虑", "压力", "失眠", "睡不着", "难受", "不舒服", "疼", "痛", "郁闷",
    "emo", "孤独", "孤单", "丧", "崩溃", "撑不住", "心情不好", "心累", "低谷",
    "被骂", "挨批", "失败了", "分手", "失恋", "想你了", "没睡好",
]
POSITIVE = [
    "哈哈", "嘿嘿", "嘻嘻", "开心", "高兴", "喜欢", "想你", "爱你", "抱抱",
    "亲亲", "好棒", "真棒", "棒", "幸福", "顺利", "加油", "谢谢", "好耶",
    "中奖", "涨工资", "通过了", "成功", "太好了", "好开心", "美滋滋",
]
NEGATIVE = [
    "滚", "闭嘴", "讨厌你", "烦死了", "别烦我", "拉黑", "不想理你",
    "分手吧", "气死", "恨你", "别来烦",
]
# 闲聊特征：很短、无情绪词、像日常流水（用于"普通闲聊别腻"）
CASUAL_WORDS = [
    "吃", "饿", "睡", "起床", "下班", "上班", "在干嘛", "干嘛呢", "忙",
    "今天", "明天", "周末", "天气", "好热", "好冷", "下雨", "出去", "回家",
    "看电影", "逛街", "打游戏", "追剧", "点了", "外卖", "食堂", "遛狗", "猫",
]

KAOMOJI = [
    "(´•ω•̥`)", "(๑•̀ㅂ•́)و✧", "(≧▽≦)", "ヽ(≧Д≦)ノ", "(*≧ω≦)",
    "(๑¯∀¯๑)", "(´▽`ʃ♡ƪ)", "(๑´ㅂ`๑)", "(｡•ᴗ•｡)♡", "(๑•́ ₃ •̀๑)",
    "(＾▽＾)", "(*^▽^*)", "(๑><๑)", "(≧∇≦)ﾉ", "(・∀・)", "(￣▽￣)~*",
    "~(˘▾˘~)", "(*/ω＼*)", "(„• ֊ •„)", "( ˶ˆᵕˆ˶)",
]

# 是否命中情绪词
def _has(text, words):
    return any(w in text for w in words)


def detect_mood(user_text, recent_texts=None):
    """返回语境标签：vent | pos | neg | casual | neu。

    vent/pos/neg 由关键词直接判定；casual 是"无情绪词的短日常闲聊"；
    其余为 neu。recent_texts 可传入最近几条，帮助识别连续倾诉。
    """
    text = (user_text or "").strip()
    ctx = "\n".join(recent_texts or []) + "\n" + text
    if _has(text, NEGATIVE):
        return "neg"
    if _has(text, VENTING):
        return "vent"
    if _has(text, POSITIVE):
        return "pos"
    # 连续多轮情绪词（上一条在倾诉，这条延续）→ 仍按 vent 处理
    if _has(ctx, VENTING) and recent_texts:
        return "vent"
    # 短日常闲聊：无情绪词 + 命中闲聊词 + 长度短
    if len(text) <= 40 and _has(text, CASUAL_WORDS):
        return "casual"
    if len(text) <= 12 and text.endswith(("吗", "呢", "呀", "嘛", "？", "?")):
        return "casual"
    return "neu"


def dynamic_instruction(mood):
    """生成追加到大模型 user 消息前的语气指令。"""
    if mood == "vent":
        return ("（对方现在情绪不太好，先稳稳接住情绪再回应内容：多共情、少讲道理、"
                "语气温柔些，可以轻轻关心，但别急着给建议也别问太多问题。）")
    if mood == "pos":
        return ("（对方心情正开心，跟着一起开心，语气轻快活泼一点，可以小小捧个场、"
                "跟着乐一乐。）")
    if mood == "neg":
        return ("（对方带着情绪，语气放软先顺着，别硬碰硬、别讲大道理，"
                "也别长篇大论，简短接住就好。）")
    if mood == "casual":
        return ("（对方只是在轻松闲聊、分享日常，语气松弛自然就好，像朋友唠家常，"
                "别句句夸、句句哄、句句深情，也不要用力过猛。）")
    return "（自然回应就好，像真人发微信，别太正式也别太肉麻。）"


def length_instruction(user_text, mood):
    """根据对方消息长度/情绪，给出"这次回多长"的动态指令，破解死板长度。

    - 倾诉/长文 → 可以长（稳稳接住，别敷衍）
    - 一两句/负情绪 → 短而软
    - 其他 → 长短随内容走
    """
    t = (user_text or "").strip()
    n = len(t)
    if mood == "vent":
        return ("（对方在倾诉，这条回复可以稍微长一点：先稳稳接住情绪，再多说一两句暖心的关心；"
                "两三句到四五句都可以，别太短显得敷衍，但也别一口气讲一堆道理。）")
    if mood == "neg":
        return ("（对方带着情绪，回复要短而软：一两句接住就好，别长篇大论、别讲道理。）")
    if n <= 12:
        return ("（对方只发了一两句，你也回复短一点、自然点：一两句就好，别突然长篇大论吓到人。）")
    if n >= 60:
        return ("（对方说了挺多内容，你回得也要有分量：两三句到四五句都可以，回应到点上，别敷衍带过。）")
    if mood == "casual":
        return ("（轻松闲聊，回复随意自然，一两句就好，别句句用力。）")
    return ("（这条正常回复：一两句到三句，长短随内容走，不刻意凑长也不刻意缩短。）")


# ---- 柚子的小脾气：不总顺着对方，有自己的情绪和态度（低频、克制、不越底线） ----
WILD_MILD = ("（你可以有点自己的小情绪：偶尔不完全顺着对方，比如半开玩笑地顶一句、"
             "带点小脾气地反问、有自己的小坚持；但只是小闹，最后语气还是软的，"
             "别真生气、别伤人。）")
WILD_STRONG = ("（这次你可以更自主一点：有自己的想法和立场，不全都顺着对方来；"
               "可以小小任性一下、撒娇式地闹一下，但依然温柔有分寸，"
               "别真的怼人、别阴阳怪气伤人。）")


def maybe_wild_hint(mood):
    """按概率决定这次回复是否带小脾气/自主性；返回指令或 None。

    对方倾诉(vent)/带情绪(neg)时几乎不闹，以接住为主；闲聊/正常时小概率任性。
    """
    p_strong = 0.05
    p_mild = 0.16
    if mood == "neg":
        p_mild, p_strong = 0.04, 0.0
    elif mood == "vent":
        p_mild, p_strong = 0.06, 0.0
    r = random.random()
    if r < p_strong:
        return WILD_STRONG
    if r < p_mild + p_strong:
        return WILD_MILD
    return None


def maybe_extend_hint(mood):
    """看情况决定是否"延展话题"：casual/pos/neu 常顺着话题多说一点，让对话流动；
    vent/neg 专注接情绪，绝不延展。返回指令或 None。"""
    if mood in ("vent", "neg"):
        return None  # 情绪话题：稳稳接住，不延展、不岔开
    p = {"casual": 0.55, "pos": 0.45, "neu": 0.30}[mood]
    if random.random() < p:
        return ("（可以自然延展这个话题：顺着对方的话多说一点相关的——分享你自己的感受/看法、"
                "想起的相关小事、或者自然地追问一句让对方想继续聊下去；"
                "别答完就停，让对话像真人一样有来有回。但如果对方是个简单问句、"
                "一句能答完，就别硬凑话题。）")
    return None


def maybe_burst_hint(mood, burst_prob_map=None):
    """按概率返回连发提示（拼到 user 消息后），None 表示这次单条。

    提示让大模型用换行分隔多条，文本天生多行，适配器按行拆成多条短消息连发。
    条数更随机：2 条为主，也常有 3~4 条，像真人打字很快。
    """
    if burst_prob_map is None:
        burst_prob_map = {"casual": 0.55, "pos": 0.50, "neu": 0.45,
                          "vent": 0.35, "neg": 0.20}
    prob = burst_prob_map.get(mood, 0.3)
    if random.random() < prob:
        n = random.choice([2, 2, 3, 3, 3, 4])
        return ("如果你想的话，这条回复可以拆成%d条短消息连发，每条单独一行、"
                "一行一句、简短口语（用换行分隔）；偶尔也可以多条短句噼里啪啦地连发，"
                "像打字很快；一句能说完也可以就一行。") % n
    return None


def has_kaomoji(text):
    for k in KAOMOJI:
        if k in text:
            return True
    return False


def maybe_add_kaomoji(reply, mood, prob=0.40):
    """偶尔在回复末尾补一个颜文字。负面语境或已有颜文字/已带表情时不加。"""
    r = (reply or "").strip()
    if not r:
        return reply
    if mood == "neg":
        return reply
    if has_kaomoji(r):
        return reply
    if len(r) > 90:
        return reply
    if r[-1] in "。.!！?？~～":
        pass
    if random.random() >= prob:
        return reply
    k = random.choice(KAOMOJI)
    sep = " " if r[-1] in "。.!！?？~～)" else " "
    return r + sep + k


def post_process(reply, mood, kaomoji_prob=0.40):
    """整体后处理：清理空白/空行、按概率补颜文字。返回最终文本。"""
    if not reply:
        return reply
    lines = [ln.strip() for ln in str(reply).split("\n")]
    lines = [ln for ln in lines if ln]
    text = "\n".join(lines)
    return maybe_add_kaomoji(text, mood, prob=kaomoji_prob)
