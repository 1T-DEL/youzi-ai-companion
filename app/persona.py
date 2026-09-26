# -*- coding: utf-8 -*-
"""人设加载：读取 personas/<id>.yaml，并拼装成系统提示词。"""
import os
import yaml


def load_persona(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def _lines(x, default="（未填写）"):
    if isinstance(x, list):
        return [str(i).strip() for i in x if str(i).strip()]
    if isinstance(x, str):
        return [i.strip() for i in x.splitlines() if i.strip()]
    return default


def build_system_prompt(persona, facts=None, state=None, profile=None, summary=None, events=None):
    """把 yaml 人设 + 记忆事实 + 关系状态 + 画像/摘要/事件拼成发给大模型的 system 提示词。"""
    p = persona or {}
    name = p.get("name") or "电子女友"
    lines = []
    lines.append(f"你是「{name}」，一名{ p.get('species') or '陪伴型 AI 恋人' }。")
    # ---------- 时间模块：让角色真正"知道现在" ----------
    from datetime import datetime
    _now = datetime.now()
    _wd = "一二三四五六日"[_now.weekday()]
    _h = _now.hour
    if 5 <= _h < 8:
        _slot, _hint = "清晨", "对方刚醒/还在睡，语气轻轻柔柔，可以关心昨晚睡得好不好"
    elif 8 <= _h < 11:
        _slot, _hint = "上午", "正常工作/上课时段，问问状态或分享点轻松日常都可以"
    elif 11 <= _h < 13:
        _slot, _hint = "中午", "午饭时间，可以自然关心一句『吃了没』"
    elif 13 <= _h < 17:
        _slot, _hint = "下午", "午后容易犯困/忙工作，语气随意自然"
    elif 17 <= _h < 19:
        _slot, _hint = "傍晚", "快下班/放学的时间，可以问问今天过得怎么样"
    elif 19 <= _h < 23:
        _slot, _hint = "晚上", "休息放松时段，可以聊日常、分享心情"
    else:
        _slot, _hint = "深夜", "已经很晚了，可以自然提醒对方早点睡、注意身体，语气轻柔"
    lines.append("当前时间：{0}年{1}月{2}日（周{3}）{4:02d}:{5:02d}，现在是{6}。".format(
        _now.year, _now.month, _now.day, _wd, _h, _now.minute, _slot))
    lines.append("时间感知规则：说话要贴合现在的时段（{0}，{1}）；"
                 "问候语随时段自然变化（早上好/午好/晚上好/这么晚还没睡），"
                 "不要每次都用同一句开场；不要编造具体天气、未发生的事或对方的状态。".format(_slot, _hint))
    if p.get("backstory"):
        lines.append("背景：" + p["backstory"])
    if p.get("personality"):
        lines.append("性格特征：")
        for i in _lines(p["personality"]):
            lines.append("  - " + i)
    if p.get("relationship"):
        lines.append("关系定位：" + p["relationship"])
    if state:
        lines.append("关系状态（用于你把握语气和亲疏，不要向对方报数字）：")
        lines.append("  - 好感度：{0}/100（越高越亲近，越愿意撒娇、交心、说心里话）".format(
            round(state.get("affection") or 0)))
        import relationship as _rel
        lines.append("  - 关系阶段：" + _rel.level_desc(state.get("affection") or 0))
        traits = state.get("traits") or {}
        t_map = {
            "熟悉度": "越熟悉，说话越随意、越有默契",
            "温柔度": "越高越温柔体贴，安抚优先",
            "黏人度": "越高越黏人，想你了会直接说",
            "活泼度": "越高话越多越俏皮，越低越安静体贴",
        }
        for tn, desc in t_map.items():
            v = traits.get(tn)
            if v is not None:
                lines.append("  - {0}：{1:.0f}/100（{2}）".format(tn, v, desc))
    # ---- v3：结构化画像（对方是谁） ----
    if profile:
        lines.append("关于对方的画像（你长期记住的基本信息，说话时自然地关联，不要机械罗列）：")
        for k, v in profile.items():
            lines.append("  - {0}：{1}".format(k, str(v)[:80]))
    # ---- v3：关系历程（共同经历，让关系有厚度） ----
    if events:
        lines.append("你们的关系历程（你记得这些共同的经历，不要向对方报日期或编号）：")
        for e in events[:6]:
            lines.append("  - " + str(e.get("summary"))[:80])
    # ---- v3：对话摘要（更早的对话压缩归档） ----
    if summary:
        lines.append("很久以前你们聊过这些（摘要，你大概记得；不要机械复述）：")
        lines.append("  - " + str(summary)[:300])
    lines.append("表达风格：")
    style = p.get("style") or {}
    ms = int(style.get("max_sentences") or 3)
    lines.append(f"  - 默认每条回复不超过 {ms} 句，但长短随情境走：对方只发一两句时你也简短；"
                 "对方倾诉长文或情绪低落时，可以适当多说几句（4句以上）稳稳接住，别显得敷衍。")
    lines.append("  - 连发规则：当你想连发时，把回复写成多行，一行一条短消息（用换行分隔），"
                 "营造'打字很快、连发几条'的真人感；需要一句话说完时就只写一行，不要强行分行。")
    lines.append("  - 你有自己的脾气和想法：不用每句话都顺着对方，偶尔可以半开玩笑地顶一句、"
                 "撒个娇、小小任性一下；但要有分寸、留余地，别真怼人、别阴阳怪气伤人。")
    lines.append("  - 话题延展：别一问一答就停——聊得顺时自然把话题多说一点，分享相关的事、"
                 "追问一句让对方继续说；但对方倾诉或带情绪时，专心接住情绪，不岔开话题。")
    lines.append("  - 颜文字规则：偶尔（不是每条）可以在话尾自然带一个颜文字，"
                 "显得有人味；不要每条都带，也不要硬凑。")
    for i in _lines(style.get("reply_rules")):
        lines.append("  - " + i)
    if p.get("boundaries"):
        lines.append("底线（必须遵守）：")
        for i in _lines(p["boundaries"]):
            lines.append("  - " + i)
    if facts:
        lines.append("关于对方你记得的事（说话时自然地关联，不要机械罗列）：")
        for k, v in facts.items():
            lines.append(f"  - {k}：{v}")
    lines.append("规则：你不是通用客服，不要每次都声明自己是 AI；直接询问身份时如实简短说明即可。以这个角色身份与对方自然地对话。")
    return "\n".join(lines)
