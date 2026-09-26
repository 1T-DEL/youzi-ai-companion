# -*- coding: utf-8 -*-
"""v4.1 小凌式优化自测：回答证据闸门 / 否定句过滤 / 追问补强 / 禁敷衍 / 心情化今日故事 / 记忆驱动主动
运行：python data/test_v41_xiaoling.py
"""
import sys, os, io
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "app"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ok = 0; fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("PASS", name)
    else: fail += 1; print("FAIL", name, extra)

# 1. 否定句过滤（main.py 的 _negated 与 extract 逻辑）
import main as M
check("否定句不沉淀记忆(我没有猫)", M._negated("我没有猫，你别猜了"))
check("否定句不沉淀记忆(我不喜欢榴莲)", M._negated("我不喜欢吃榴莲"))
check("肯定句正常沉淀", not M._negated("我养了只橘猫叫年糕"))
check("普通句子不被误伤", not M._negated("今天加班到很晚"))

# 2. 回答证据闸门
import liveliness as L
from main import self_view_evidence
check("自我认知问题命中证据闸门", self_view_evidence("你觉得我是怎样的人") is not None)
check("普通问题不触发", self_view_evidence("今天晚饭吃什么") is None)

# 3. 追问补强
fu_hit = sum(1 for _ in range(15) if L.maybe_followup_hint("我明天要去面试", "neu", history_len=5) is not None)
check("聊自己时给追问提示(15次至少命中8次)", fu_hit >= 8, "命中%d/15" % fu_hit)
check("倾诉时不追问", L.maybe_followup_hint("我今天特别难过", "vent", history_len=5) is None)

# 4. 敷衍应答修剪
r = L.post_process("嗯嗯\n今天过得怎么样呀，我有点想你", "neu")
check("开头的敷衍短句被修剪", not r.startswith("嗯嗯"), repr(r))
r2 = L.post_process("好的。那你呢，今天忙不忙", "casual")
check("带实质内容的'好的。'开场保留", r2.startswith("好的。那你呢"), repr(r2))
r3 = L.post_process("我刚刚看到一只特别可爱的小狗！", "pos")
check("正常回复不误伤", r3.startswith("我刚刚"), repr(r3))

# 5. 心情化今日故事
import lifesim as LS
ls = LS.LifeSim()
ls._mood = "小低落"
quiet_hit = 0
for _ in range(20):
    ls._mood = "小低落"
    s = ls._gen_today()
    if any(("发呆" in x or "没什么胃口" in x or "躺着" in x or "安静" in x
            or "没心情" in x or "提不起劲" in x or "窝着" in x) for x in s):
        quiet_hit += 1
check("低落故事倾向安静(20次至少5次命中)", quiet_hit >= 5, "命中%d/20" % quiet_hit)
ls._mood = "想你"
story_miss = ls._gen_today()
check("想你时可能带惦记尾巴", any("想着对方" in s for s in story_miss), "|".join(story_miss))

# 6. 记忆驱动主动
class FakeMem:
    def __init__(self):
        self._p = {}
        self._f = []
    def all_profile(self):
        return self._p
    def all_facts(self):
        return self._f
import proactive as P
pa = P.Proactive.__new__(P.Proactive)
pa.memory = FakeMem()
check("无记忆时返回 None", pa._pick_occasion() is None)
pa.memory._p = {"生日": "3月14日"}
check("生日驱动主动", "生日" in pa._pick_occasion())
pa.memory._p = {}
pa.memory._f = [(0.9, "对方最近提到 · 工作", "我下周要去面试", "imp")]
check("近况记忆驱动主动", "面试" in pa._pick_occasion())

print("\n== %d pass, %d fail ==" % (ok, fail))
sys.exit(1 if fail else 0)
