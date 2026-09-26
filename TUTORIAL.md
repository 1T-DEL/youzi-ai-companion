# -*- coding: utf-8 -*-
"""柚子AI伴侣 接入教程

> 从空状态到能跑的完整清单。按顺序做就行，跳过「可选」的都是最后再加的。

---

## 阶段 1 · 让对话先跑起来（30 分钟）

最低只需要填 3 个字段，对话就能跑通。

### 1.1 选一家对话模型服务商

**国内（注册简单、便宜、中文好）：**

- **DeepSeek 深度求索** —— 注册送免费额度，deepseek-chat 约 ¥0.14/百万 token，最便宜
- **阿里云百炼** —— 注册送免费额度，qwen-plus 中文特别强，后续接语音也能用同一账号
- **硅基流动 SiliconFlow** —— 注册送额度，DeepSeek / Qwen / Llama 都有，还支持语音

**海外：**

- **OpenAI** —— gpt-4o-mini 最便宜，但需海外卡
- **Moonshot Kimi** —— 长文档场景
- **智谱 GLM** —— 注册送额度

### 1.2 拿 API Key

- 注册账号 → 实名认证（国内都要）
- 控制台 → API Keys → 创建新 Key
- 复制粘贴下来（形如 `sk-abc123...`，只显示一次）

### 1.3 在 setup.html 填好对话配置

打开项目根目录的 `setup.html`，第 1 节「**大模型接口**」：

1. 顶部下拉直接选服务商（地址和模型名会自动填好）
2. 把 Key 粘到 `API Key` 输入框
3. 其他全保持默认即可

### 1.4 生成、下载

1. 滚到第 8 节点「**生成配置**」
2. 看弹层「**接入体检**」没有红色 ❌ 就 OK
3. 下载 `.env` 和 personas 的 yaml 两个文件

### 1.5 放进项目并启动

```powershell
# 把文件放到项目根目录
#   .env        -> 项目根\.env
#   youzi.yaml  -> 项目根\personas\youzi.yaml

cd 项目根目录
.\setup.ps1          # 第一次需要装依赖
.\run.ps1            # 启动服务
```

浏览器打开 http://127.0.0.1:8000 就能开始对话。

---

## 阶段 2 · 接入 QQ（可选，30 分钟）

走「**官方机器人**」最合规，可私聊、可群 @、可主动推送。

### 步骤

1. 打开 https://bot.q.qq.com → 登录 → 创建机器人
2. 控制台 → 开发设置 → 拿到 `AppID` 和 `AppSecret`（AppSecret 只显示一次，先存好）
3. setup.html 第 3 节「**QQ 接入**」：
   - 勾选「**启用 QQ 官方机器人**」
   - 填 `AppID`、`AppSecret`
   - `INTENTS` 保持默认 `33554432`（要收群 @ 消息时再按控制台给的位掩码调整）
4. 重新生成 `.env`、重启服务
5. 用 QQ 加机器人为好友 → 直接对话；拉到群里 @ 它也能回

### 替代：OneBot 11（个人 QQ 适配器）

适合已有 NapCat / Lagrange 适配器在跑：

1. 启动适配器，拿到 WebSocket URL（如 `ws://127.0.0.1:3001`）和 access token（可选）
2. setup.html 第 3 节底部勾选「**启用 OneBot 11 适配器**」→ 填 URL 和 token
3. 重启服务

**注意**：用个人 QQ 号自动化有被限制的风险，先确认平台条款。

---

## 阶段 3 · 接入微信公众号（可选，1-2 小时，比 QQ 麻烦）

公众号**只能被动回复**，受 5 秒硬限制，主动推送要走自建网关。

### 3.1 准备公众号

- 注册订阅号（个人订阅号即可）
- 「开发 → 基本配置」→ 设一个 Token（自己起一个字符串，比如 `youzi-token-2026`，记下来）
- 拿到 AppID 和 AppSecret（公众号侧用，**不是**填到 setup.html 里）

### 3.2 把本地服务暴露到公网

```powershell
# cloudflared（推荐，免费）
cloudflared tunnel --url http://127.0.0.1:8000
```

跑起来会得到一个 `https://xxx.trycloudflare.com` —— 这就是公网地址。

### 3.3 在 setup.html 填

第 4 节「**微信接入**」：
- `WECHAT_TOKEN` 填与公众号后台**完全一致**的 Token
- 其他微信侧字段先不填

### 3.4 在公众号后台配回调

- 服务器地址（URL）：`https://你的cloudflared域名/wechat/callback`
- Token：同上
- 消息加解密方式：**明文模式**（先选这个）
- 提交 —— 显示「token 验证成功」即可

### 3.5 测试

关注公众号、发任意文字，应该看到回复。

### 想要主动推送？

公众号本身不能任意主动推送。要让机器人主动找你聊：
- 自建合规微信网关（不是逆向微信、不是 hook）
- setup.html 第 4 节底部勾选「**启用自建微信网关**」→ 填网关 URL 和密钥

---

## 阶段 4 · 加语音（可选，30 分钟）

### 路线 A：OpenAI 兼容（最省事）

保持第 6 节语音后端为 **openai**（默认）：

- 如果对话也用 OpenAI，Key 共用一个 `LLM_API_KEY` 就行
- 如果对话用别家，再拿一个 OpenAI Key 填 `LLM_API_KEY`（共用同一字段），或者让对话也换到 OpenAI

| 字段 | 推荐值 |
| --- | --- |
| `ASR_MODEL` | `whisper-1` |
| `TTS_MODEL` | `tts-1` |
| `TTS_VOICE` | `nova`（年轻活泼）/`alloy`（中性）/`shimmer`（温柔） |

国内可换硅基流动：`SenseVoiceSmall` + `FishSpeech-1.5`，同 OpenAI 兼容接口、价格更低。

### 路线 B：阿里云百炼（中文更强）

第 6 节下拉切到「**阿里云百炼 DashScope**」：

- `DASHSCOPE_API_KEY` 填百炼控制台签发的 `sk-...`
- `ASR_MODEL` 填 `paraformer-v2`（流式录可以替换 `paraformer-realtime-v2`）
- `TTS_MODEL` 填 `cosyvoice-v2`
- `TTS_VOICE` 选 `longxiaoyue` 或 `sambert-zhixiajia`（活泼）

代码已经支持双引擎，无需改任何 Python。

---

## 阶段 5 · 主动问候（可选，5 分钟）

让机器人主动找你聊天。

### 步骤

1. setup.html 第 5 节勾选「**启用主动问候**」
2. `INTERVAL_MINUTES` 默认 180（3 小时一次，调小容易被嫌烦）
3. `QUIET_START=23`、`QUIET_END=8`（晚 11 点到早 8 点不打扰）
4. 重生成 `.env`、重启服务
5. 重启后主动问候按间隔自动运行（无额外命令）

主动消息只根据已有记忆生成，不会瞎编「刚发生的事」。

---

## 阶段 6 · 上线前 5 件事

按重要程度排：

1. **配白名单** —— 第 7 节 `ALLOWLIST`。留空 = 任何人都能用，会被滥用且烧钱
2. **别把 `.env` 传出去** —— 里面是 AppID、AppSecret、API Key 全套
3. **HOST 保持 127.0.0.1** —— 公网访问走反向代理（cloudflared / nginx）
4. **数据库别放同步盘或公开目录** —— `data/pimate.db` 里有全部聊天记录
5. **测试核心命令** —— 启动后跑一遍聊天与管理页 `http://127.0.0.1:8000/admin`

---

## 阶段 7 · 调人设

第 2 节人设全在 setup.html 里改，也可用管理页 `http://127.0.0.1:8000/admin` 在线微调：

- 角色名（让她自称）
- 性格特征（每行一条）
- 回复规则（决定「先共情再讲道理」之类的核心行为）
- 底线（不索取隐私、紧急情况转介真实服务）

填完重生成 yaml 替换原文件 → 重启服务即可，**不用改任何 Python 代码**。

想要「活泼元气」可以加几条：

- 语气轻快、有精神，但不咋咋呼呼
- 主动分享自己的小情绪和小发现
- 偶尔轻微俏皮，不用过度叠词

---

## 常见坑

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| 浏览器打不开页面 | 服务没起 / 端口被占 | 看 PowerShell 报错，可能要换 `PORT` |
| 启动后回的是默认演示文本 | Key 没填 / 模型名错 / `LLM_BASE_URL` 多带斜杠 | 看体检报告里的红 ❌ |
| QQ 收不到消息 | 机器人控制台未发布 / 没配 Intent | 检查 bot.q.qq.com 控制台 |
| 微信「该公众号暂时无法提供服务」 | 模型响应 > 5 秒 | 换更快模型 / 缩短上下文 |
| 语音按钮变灰 | `ASR_MODEL` 没填 | 填上 + 重启 |
| 语音合成 404 | 选了 DashScope 但模型名是 OpenAI 风格的 | 切到 `dashscope` 后端时模型也要换成 paraformer/cosyvoice/sambert-* |
| 体检一堆警告 | 多字段未填，但服务能跑 | 警告通常只是「这功能用不了」，按需修 |

---

## 表情包与头像素材

仓库不直接存二进制图片，素材以 `assets_pack.py` 内嵌 base64 打包（运行 `python assets_pack.py` 自动还原到 `data/stickers`、`data/gif_library` 与 `static/avatar.jpg`）。

也可以不还原素材直接跑：首次启动 `app/sticker_updater.py` 会自动生成字牌/表情贴纸，`python data/make_gifs.py` 会生成 5 张动图，头像缺省时页面显示角色名首字。

---

## 万一还是跑不起来

1. 先看 setup.html 的体检报告
2. 看 PowerShell 控制台最后几行（错误堆栈通常会打出来）
3. 看 `data/pimate.db` 是否能正常创建（写权限问题）
4. 实在不行 —— 把体检结果截图发到项目 Issue，我们可以看出现状给你指点
