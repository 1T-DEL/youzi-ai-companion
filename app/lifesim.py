# -*- coding: utf-8 -*-
"""LifeSim —— 三层时间尺度数字生命模拟（"苏晚"式理念）

让 AI 从"反应器"变成"发生器"：她有自己的生活时间线、身体状态、
情绪惯性和人生故事，不依赖用户的输入节奏而独立运转。

三层时间尺度：
  1. 短期层（秒-分钟）：此刻在做什么、身体状态、即时心情
  2. 中期层（小时-天）：今天的生活轨迹（做什么、去哪、吃什么），每天自动生成
  3. 长期层（周-月）：人生故事与关系进展（由 persona / relationship 提供，此处聚合）

情绪惯性：心情不是随机跳变的，有持续期（惯性），用户情绪会推动它转向。
物理阻碍：身体状态（饿/困/累/不舒服）会自然影响她的表达。
"""
import random
import time
from datetime import datetime

# 心情池（与 proactive 共用语义）
MOODS = ["平静", "开心", "想你", "无聊", "小低落", "兴奋"]
# 心情 → 简短行为提示（注入 prompt 用）
_MOOD_BLOCK = {
    "平静": "心情平静，状态很放松",
    "开心": "心情不错，有点小开心",
    "想你": "心里正想着对方，有点黏人",
    "无聊": "有点无聊，想找人聊两句",
    "小低落": "心情有一点低落，想要被哄一哄",
    "兴奋": "今天有点小兴奋，话会多一些",
}
# 身体状态池（物理阻碍：她的身体也有自己的节奏）
_BODY_POOL = [
    "正常", "正常", "正常", "有点饿", "有点困", "有点累", "不太舒服（轻微）", "饿了想吃东西",
]
# 今日生活轨迹模板：每个时段多个选项（星期几不同）。v4.1 扩充 + 按心情倾斜，
# 让"今天的生活"不再是一成不变的随机拼盘，而是贴合她的心情状态。
_DAY_PART = {
    "morning": [
        "上午有课", "上午在家收拾房间", "上午去图书馆看了会儿书", "上午出门溜达了一圈",
        "上午赖床到很晚才起", "上午在忙作业/工作", "上午去买了杯咖啡回来发呆",
        "上午坐在窗边看了很久的云", "上午把攒了好久的衣服洗了", "上午去公园走了走，空气很好",
        "上午在听歌，什么也没干成",
    ],
    "noon": [
        "中午和同学一起吃饭", "中午随便点了份外卖", "中午自己煮了面", "中午没胃口，吃了点水果",
        "中午和室友去吃了食堂", "中午做了个拿手菜，还挺得意", "中午对付了几口就继续忙了",
        "中午出去吃了碗热乎的面", "中午看着外卖软件纠结了半天",
    ],
    "afternoon": [
        "下午在图书馆泡了一下午", "下午追了一下午的剧", "下午去超市买了好多零食",
        "下午在健身房锻炼了一会儿", "下午窝在家里发呆听歌", "下午和同学逛了街",
        "下午把一直想看的电影看完了", "下午睡了个长长的午觉，醒来有点恍惚",
        "下午在写东西/赶进度，效率还行", "下午出门走了走，晒了会儿太阳",
        "下午蹲在阳台发呆，脑子里想东想西",
    ],
    "evening": [
        "傍晚去操场散了散步", "傍晚和室友吃了晚饭", "傍晚在阳台吹了会儿风",
        "傍晚自己做了顿晚饭", "傍晚骑车出去转了转", "傍晚看天慢慢暗下来，发了一会儿呆",
        "傍晚遛了个弯，回来买了瓶饮料", "傍晚听着歌收拾了会儿东西",
    ],
    "night": [
        "晚上在追剧", "晚上在听歌发呆", "晚上打了会儿游戏", "晚上在看书",
        "晚上和室友聊天到很晚", "晚上在整理房间", "晚上刷手机刷着刷着就晚了",
        "晚上窝着听歌，舍不得睡", "晚上把白天没做完的事收了个尾",
        "晚上在想事情，想了很久",
    ],
}
# 低落/无聊时额外倾向的安静选项（混合进池子，让她"情绪低落时连生活都安静"）
_QUIET_POOL = {
    "morning": ["上午赖床到很晚才起", "上午发呆了一上午，什么也没干成", "上午没怎么出门，就在屋里待着"],
    "noon": ["中午随便吃了点东西，没什么胃口", "中午对着饭发呆，随便扒拉了几口"],
    "afternoon": ["下午窝着没出门，发呆/刷手机", "下午睡了一觉，醒来有点恍惚", "下午什么也不想干，就在床上躺着"],
    "evening": ["傍晚自己在阳台发了会儿呆", "傍晚没出门，安静地待着", "傍晚看着窗户外面发愣"],
    "night": ["晚上安静地听歌发呆", "晚上没心情玩，早早躺下了", "晚上想找人说说话，又不知道说什么"],
}
_BODY_BY_TIME = {
    (11, 13): "有点饿",      # 饭点
    (13, 15): "有点困",      # 午困
    (22, 26): "有点困",      # 深夜
}


class LifeSim:
    def __init__(self, memory=None, log=None):
        self.memory = memory
        self.log = log or print
        # ---- 短期层：即时状态 ----
        self._mood = random.choice(MOODS)
        self._mood_strength = random.choice([1, 1, 2, 2, 3])   # 1轻 3浓
        self._mood_until = time.time() + random.uniform(40, 90) * 60  # 情绪惯性期
        self._body = random.choice(_BODY_POOL)
        self._body_check = time.time()
        # ---- 中期层：今日生活轨迹（每天生成一次） ----
        self._story_date = None
        self._today_story = []
        # ---- 事件回响：最近一次被用户情绪冲击后的余韵 ----
        self._echo = ""           # 例如"刚才对方说了难过的事，你还惦记着"
        self._echo_until = 0
        # ---- 成长层（小凌式：出生档案 vs 后天成长，证据闸门） ----
        # 出生档案 = persona（先天基线，不动）；此处只记录"后天成长变更"
        self._growth_log = []      # [ ("YYYY-MM-DD", mood_sign) ... ] 情绪事件证据
        self._growth_applied = {}  # {trait: total_delta} 已生效的成长变更（保守上限）

    # ============ 对外接口 ============
    def mood(self):
        """当前心情（字符串）。"""
        self._tick()
        return self._mood

    def note_user_mood(self, mood):
        """用户消息的情绪会推动她的情绪转向（情绪惯性被事件打破）。"""
        now = time.time()
        if mood == "pos":
            self._mood = random.choice(["开心", "想你", "兴奋"])
            self._mood_strength = 2
            self._mood_until = now + random.uniform(25, 50) * 60
            self._echo = "刚才和对方聊天很开心，心里还热乎乎的"
        elif mood == "vent":
            self._mood = random.choice(["想你", "小低落"])
            self._mood_strength = 3
            self._mood_until = now + random.uniform(40, 80) * 60
            self._echo = "对方刚才说了些心里话，你还惦记着，想多陪陪ta"
        elif mood == "neg":
            self._mood = "小低落"
            self._mood_strength = 2
            self._mood_until = now + random.uniform(20, 40) * 60
            self._echo = "刚才好像让对方有点不开心，你心里有点慌，想好好哄哄ta"
        elif mood == "shy":
            self._mood = "想你"
            self._mood_strength = 2
            self._mood_until = now + random.uniform(20, 40) * 60
            self._echo = "刚才说到有点害羞的话，你还有点脸红"
        else:
            self._echo = ""
        self._echo_until = now + 2 * 3600  # 余韵持续 2 小时
        self._growth_evidence(mood)

    # ---- 成长证据闸门（小凌式） ----
    _GROWTH_MAP = {
        "pos":  ("活泼度", +1),    # 常被逗开心 → 变活泼
        "vent": ("温柔度", +1),    # 常接住低落 → 变温柔
        "neg":  ("温柔度", +1),    # 闹过别扭又和好 → 更懂体谅
        "shy":  ("黏人度", +1),    # 常被说脸红的话 → 变黏人
        "想你": ("黏人度", +1),
    }
    _GROWTH_CAP = 5.0   # 每个特质累计成长上限（保守，防止性格被一句话改掉）
    _GROWTH_MIN_DAYS = 3  # 同一信号需跨 ≥3 个不同日期才产生成长变更

    def _growth_evidence(self, mood_sign):
        """记录一次情绪事件证据（带日期）。"""
        today = datetime.now().strftime("%Y-%m-%d")
        self._growth_log.append((today, mood_sign))
        # 只保留最近 14 天的证据（日期字符串 YYYY-MM-DD 可字典序比较）
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
        self._growth_log = [(d, s) for d, s in self._growth_log if d >= cutoff]
        if len(self._growth_log) > 200:
            self._growth_log = self._growth_log[-200:]

    def growth_tick(self):
        """跨天证据汇总 → 产生成长变更（单次小步、累计设上限、写入性格）。"""
        if not self._growth_log:
            return None
        # 统计每个信号出现的"不同日期数"（普通 dict 存 set）
        days = {}
        for d, s in self._growth_log:
            days.setdefault(s, set()).add(d)
        applied = []
        for sign, dates in days.items():
            if len(dates) < self._GROWTH_MIN_DAYS:
                continue
            info = self._GROWTH_MAP.get(sign)
            if not info:
                continue
            trait, delta = info
            cur_delta = float(self._growth_applied.get(trait) or 0)
            if abs(cur_delta + delta) > self._GROWTH_CAP:
                continue  # 达到上限：不再成长（人格需要"慢慢长"）
            self._growth_applied[trait] = cur_delta + delta
            if self.memory is not None:
                try:
                    base = float(self.memory.get_trait(trait, 40.0))
                    self.memory.set_trait(trait, base + delta)
                    self.memory.add_fact(
                        "柚子自己的成长", "%s +%d（连续%d天被%s情绪影响）" % (
                            trait, int(delta), len(dates), sign),
                        meaning=0.75, emotion=0.4)
                except Exception as e:
                    self.log("[LifeSim] 成长变更失败：" + str(e))
            applied.append("%s %+d" % (trait, int(delta)))
        if applied:
            # 已生效的信号清除证据（避免同一批证据反复叠加）
            keep_days = set()
            for d, s in self._growth_log:
                info = self._GROWTH_MAP.get(s)
                if info and len(days[s]) >= self._GROWTH_MIN_DAYS:
                    keep_days.add((d, s))
            self._growth_log = [(d, s) for d, s in self._growth_log
                                if (d, s) not in keep_days]
        return "、".join(applied) if applied else None

    def growth_state(self):
        """成长状态（供管理页/调试）。"""
        return {"applied": dict(self._growth_applied),
                "evidence_days": len(self._growth_log)}

    def today_story(self):
        """今日生活轨迹（确保每天已生成）。"""
        self._tick()
        return self._today_story

    def state_block(self):
        """生成注入 system prompt 的"此刻状态"文本块。"""
        self._tick()
        now = datetime.now()
        _wd = "一二三四五六日"[now.weekday()]
        mood = self._mood
        strength = {1: "轻轻", 2: "有点", 3: "很明显"}.get(self._mood_strength, "有点")
        lines = []
        lines.append("【此刻】{0}月{1}日（周{2}）{3:02d}:{4:02d}，你正：{5}。".format(
            now.month, now.day, _wd, now.hour, now.minute, self._now_doing()))
        if self._today_story:
            lines.append("【今天】你今天的生活轨迹（对方问起时可以自然地分享）：" +
                         "、".join(self._today_story) + "。")
        lines.append("【你的状态】心情：{0}（{1}{2}）；身体：{3}。".format(
            mood, strength, _MOOD_BLOCK.get(mood, "").replace("心情", ""), self._body))
        if self._echo and time.time() < self._echo_until:
            lines.append("【余韵】" + self._echo + "（会自然流露，不要机械播报）")
        return "\n".join(lines)

    # ============ 内部：三层状态推进 ============
    def _tick(self):
        """每轮调用前推进：情绪惯性漂移 + 身体状态刷新 + 今日故事。"""
        now = time.time()
        # 情绪惯性：惯性期内保持不变；期外 85% 延续（惯性强）、15% 小幅漂移
        if now >= self._mood_until:
            if random.random() < 0.85:
                self._mood_until = now + random.uniform(40, 90) * 60  # 延续
            else:
                idx = MOODS.index(self._mood)
                self._mood = MOODS[max(0, min(len(MOODS) - 1, idx + random.choice([-1, 1])))]
                self._mood_strength = random.choice([1, 1, 2, 2, 3])
                self._mood_until = now + random.uniform(30, 70) * 60
        # 身体状态：按时间规律 + 偶发
        if now - self._body_check > 30 * 60:
            self._body_check = now
            h = datetime.now().hour
            self._body = _BODY_BY_TIME.get(
                next((k for k in _BODY_BY_TIME if k[0] <= h or (k[1] > 23 and h < (k[1] - 24))), None),
                "正常") if random.random() > 0.3 else random.choice(_BODY_POOL)
        # 今日生活轨迹：每天生成一次（跨天重置）
        today = datetime.now().strftime("%Y-%m-%d")
        if self._story_date != today:
            self._story_date = today
            self._today_story = self._gen_today()
            if self.memory is not None:
                try:
                    self.memory.add_fact("柚子今天的生活", "；".join(self._today_story))
                except Exception as e:
                    self.log("[LifeSim] 今日轨迹写入失败：" + str(e))

    def _now_doing(self):
        """短期层：按当前时段+心情给一个"此刻动作"。"""
        h = datetime.now().hour
        pool = {
            (5, 9): ["刚醒没多久，正赖床发呆", "刚起床，慢吞吞地洗漱"],
            (9, 12): ["在忙自己的小日子，收拾/看书/放空", "窝在沙发上发呆，偶尔瞄一眼手机"],
            (12, 14): ["刚吃完饭，正犯困地窝着", "在准备午饭/刚吃完，有点懒得动"],
            (14, 17): ["在发呆/刷手机/听歌", "在整理东西，或者发呆想事情"],
            (17, 19): ["在准备吃饭/刚吃完饭", "在阳台吹风，一天快过去了"],
            (19, 23): ["在追剧/听歌，窝着很放松", "在收拾房间，一边想着对方"],
            (23, 26): ["明明该睡了但还不想睡", "困了但还撑着，想找人说话"],
        }
        k = next((k for k in pool if k[0] <= h or (k[1] > 23 and h < (k[1] - 24))), None)
        doing = random.choice(pool[k]) if k else "在过自己的一天"
        if self._mood == "想你":
            doing += "，心里还惦记着对方"
        elif self._mood == "小低落":
            doing += "，有点提不起劲"
        return doing

    def _gen_today(self):
        """中期层：生成今天的生活轨迹（5 个时段各挑一个，星期几略作区分）。

        v4.1：结合当下心情——
          - 小低落/无聊：池子里混入安静选项（情绪低落时连生活都慢下来）
          - 想你：随机给某个时段加上"心里一直想着对方"的尾巴
        不再是固定模板池的纯随机拼盘，而是"有情绪的生活"。"""
        parts = []
        order = ["morning", "noon", "afternoon", "evening", "night"]
        for k in order:
            pool = list(_DAY_PART[k])
            if self._mood in ("小低落", "无聊"):
                pool = pool + list(_QUIET_POOL.get(k, []))
            pick = random.choice(pool)
            if self._mood == "想你" and random.random() < 0.4:
                pick += "，心里一直想着对方"
            elif self._mood == "小低落" and random.random() < 0.3:
                pick += "，有点提不起劲"
            parts.append(pick)
        return parts


# 模块级便捷函数：构建时若需要单例可经 main 持有
def build(memory=None, log=None):
    return LifeSim(memory=memory, log=log)
