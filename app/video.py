# -*- coding: utf-8 -*-
"""视频解析：①识别聊天里的视频分享链接，用 yt-dlp 抓标题/作者/时长/简介；②对本地视频文件用 ffmpeg 抽帧，
交给视觉模型理解画面。结果转成文字，喂给大模型，让"柚子"能"看懂"你分享的视频。
"""
import json
import os
import re
import subprocess
import tempfile

# 常见视频平台域名（用于判断一条链接是不是视频分享）
_PLATFORM_HINTS = (
    "bilibili.com", "b23.tv", "bili2233.cn",
    "v.douyin.com", "douyin.com", "iesdouyin.com",
    "v.kuaishou.com", "kuaishou.com",
    "youtube.com", "youtu.be",
    "weibo.com", "weibo.cn",
    "ixigua.com", "iqiyi.com", "v.qq.com", "qq.com/x/cover",
    "xiaohongshu.com", "xhslink.com", "video.weibo.com",
    "tiktok.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "mangotv.com", "acfun.cn", "cctv.com", "souhu.com", "v.sogou.com",
    "zhibo.tv", "v.163.com", "open.163.com",
)
_URL_RE = re.compile(r"https?://[^\s<>\"'，。！？；：、]+", re.I)


def find_video_links(text):
    """从一段文本里找出看起来是视频分享链接的 url 列表。"""
    if not text:
        return []
    urls = _URL_RE.findall(text)
    out = []
    for u in urls:
        u = u.rstrip(".,);】]）")
        lu = u.lower()
        if any(h in lu for h in _PLATFORM_HINTS):
            out.append(u)
    return out


def _platform_name(url):
    """根据域名判断平台名，用于兜底提示。"""
    lu = url.lower()
    if "douyin" in lu or "iesdouyin" in lu:
        return "抖音"
    if "bilibili" in lu or "b23.tv" in lu or "bili2233" in lu:
        return "哔哩哔哩"
    if "kuaishou" in lu:
        return "快手"
    if "youtu" in lu:
        return "YouTube"
    if "weibo" in lu:
        return "微博"
    if "ixigua" in lu:
        return "西瓜视频"
    if "iqiyi" in lu:
        return "爱奇艺"
    if "v.qq.com" in lu or "qq.com/x/cover" in lu:
        return "腾讯视频"
    if "xiaohongshu" in lu or "xhslink" in lu:
        return "小红书"
    if "acfun" in lu:
        return "AcFun"
    if "tiktok" in lu:
        return "TikTok"
    return "视频"


def describe_video_link(url, timeout=25, cookiefile=None, vision_key=None, vision_model="qwen-vl-max"):
    """用 yt-dlp 抓链接元信息（不下载整视频），并轻量取封面图给视觉模型看一眼，
    返回中文描述；失败返回 None。
    cookiefile：Netscape 格式 cookie 文件路径（抖音等需要登录态的站点用它）。
    vision_key / vision_model：封面图视觉识别用的千问视觉密钥与模型（不传则不看封面）。
    """
    try:
        import yt_dlp
    except Exception:
        return None
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "noplaylist": True, "socket_timeout": 10, "timeout": timeout,
    }
    if cookiefile and os.path.exists(cookiefile):
        opts["cookiefile"] = cookiefile
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=False)
        if not info:
            return None
        parts = []
        if info.get("title"):
            parts.append("标题《%s》" % info["title"])
        if info.get("uploader"):
            parts.append("作者%s" % info["uploader"])
        d = info.get("duration")
        if d:
            d = int(d)
            m, s = divmod(d, 60)
            h, m = divmod(m, 60)
            parts.append("时长%s" % ("%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)))
        desc = (info.get("description") or "").strip().replace("\n", " ")[:80]
        if desc:
            parts.append("简介：" + desc)
        # 轻量视觉：只取封面小图（几十~几百KB，内存中处理，不落盘、不下载整视频）
        cover = info.get("thumbnail")
        if cover and vision_key:
            try:
                import requests
                import app.vision as vision
                r = requests.get(cover, timeout=15,
                                 headers={"Referer": url, "User-Agent": "Mozilla/5.0"})
                if r.status_code == 200 and len(r.content) > 500:
                    cd = vision.describe_image(r.content, vision_key, vision_model, max_tokens=120)
                    if cd:
                        parts.append("封面画面：" + cd)
            except Exception:
                pass
        return "，".join(parts) if parts else None
    except Exception:
        return None


def _ffmpeg_path():
    for c in ("ffmpeg", "ffmpeg.exe"):
        try:
            r = subprocess.run([c, "-version"], capture_output=True, timeout=10)
            if r.returncode == 0:
                return c
        except Exception:
            continue
    return None


def _probe_duration(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                            "-show_format", path], capture_output=True, timeout=15)
        if r.returncode == 0:
            d = json.loads(r.stdout.decode("utf-8", "ignore"))
            return float(d.get("format", {}).get("duration", 0) or 0)
    except Exception:
        pass
    return 0.0


def _extract_frames(path, max_frames=3):
    """用 ffmpeg 抽 3 帧（首、中、尾附近），返回临时 jpg 文件路径列表。"""
    ff = _ffmpeg_path()
    if not ff or not os.path.exists(path):
        return []
    duration = _probe_duration(path)
    tmp = tempfile.mkdtemp(prefix="youzi_vid_")
    ts = []
    if duration > 0:
        if duration >= 6:
            ts = [0.5, duration * 0.5, max(duration - 0.5, 0.5)]
        else:
            ts = [duration * 0.3, duration * 0.7]
    else:
        ts = [0.5]
    frames = []
    for i, t in enumerate(ts[:max_frames]):
        out = os.path.join(tmp, "f%d.jpg" % i)
        cmd = [ff, "-y", "-ss", "%.2f" % t, "-i", path, "-frames:v", "1",
               "-q:v", "3", out]
        try:
            subprocess.run(cmd, capture_output=True, timeout=20)
            if os.path.exists(out) and os.path.getsize(out) > 1000:
                frames.append(out)
        except Exception:
            pass
    return frames


def describe_video_file(path, api_key, model="qwen-vl-max", max_frames=3, log=print):
    """对本地视频文件抽帧并识别，返回"画面描述"；失败返回 None。"""
    try:
        import app.vision as vision
    except Exception as e:
        log("[视频] vision 导入失败：" + str(e))
        return None
    frames = _extract_frames(path, max_frames)
    if not frames:
        return None
    texts = []
    for fp in frames:
        with open(fp, "rb") as f:
            b = f.read()
        d = vision.describe_image(b, api_key, model, max_tokens=150)
        if d:
            texts.append(d)
    # 清理临时帧
    for fp in frames:
        try:
            os.remove(fp)
        except Exception:
            pass
    return "；".join(texts) if texts else None


def parse_video_in_message(text, cfg, log=print):
    """供适配器调用：从 incoming text 里识别视频链接/本地视频，返回要前置给大模型的描述。
    返回字符串（形如 "[对方分享了一个视频：...]"）或 None。
    cfg 为整个 .env 字典；log 用于打印诊断。
    """
    cookiefile = (cfg.get("VIDEO_COOKIES_FILE") or "").strip() or None
    out_parts = []
    # 1) 视频分享链接
    for url in find_video_links(text):
        desc = describe_video_link(url, cookiefile=cookiefile,
                                   vision_key=cfg.get("DASHSCOPE_API_KEY"),
                                   vision_model=cfg.get("VISION_MODEL") or "qwen-vl-max")
        if desc:
            out_parts.append("[对方分享了一个{}视频：{}]".format(_platform_name(url), desc))
        else:
            out_parts.append("[对方分享了一个{}视频链接，暂时没能读到内容（可能是站点需要登录态）]".format(_platform_name(url)))
    # 2) 本地视频文件路径（如 OneBot 收到的 video 段 file 指向本地路径）
    return " ".join(out_parts) if out_parts else None
