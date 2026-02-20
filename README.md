# xiuxian_helper

一个运行在 Telegram 群组/话题里的“修仙文字游戏”自动化脚本（基于 Telethon），采用插件化结构：玩家（你）在群里操作，脚本负责按规则定时/按回包触发地发送指令，并将“我发出的指令/系统回应”以更干净的日志输出。

## 功能概览

### 1) 自动闭关（默认启用）
- 发送闭关指令（默认：`.闭关修炼`，可配置）。
- 解析回包中的冷却时间（“打坐调息 N 分钟”“灵气尚未平复…请在 N秒后再试”），到点自动重试（包含 `0秒` 场景）。

### 2) 自动种植（小药园）
插件：`garden`
- 定期发送 `.小药园` 获取状态，并解析：
  - 干旱/虫害/杂草：分别执行 `.浇水` / `.除虫` / `.除草`
  - 成熟：执行 `.采药`，随后（若有空闲灵田）执行 `.播种 <种子名>`
  - 空闲：执行 `.播种 <种子名>`（一键补满空闲灵田）
- 轮询会根据“最短剩余时间”自动提前检查，避免错过成熟时间。

### 3) 星宫（观星台 + 周天星斗大阵 + 每日问安）
插件：`xinggong`
- 观星台
  - 定期发送 `.观星台`，解析引星盘状态
  - 有异常：`.安抚星辰`
  - 可收集：`.收集精华`
  - 有空闲：`.牵引星辰 <星辰>`（不指定盘号，由系统自动填补空白）
  - 同样根据“最短剩余时间”自动提前复查
- 周天星斗大阵
  - 按配置时间开始 `.启阵`，若未成功则按配置间隔重试直到成功
  - 监听“阵成通知”（包含消息编辑场景），成功后自动进入下一轮排程
  - 若收到冷却提示（如“请在X小时X分钟X秒后再次启阵”），会更新下一次可尝试时间，避免在冷却期内刷屏
  - 自动助阵：检测到他人启阵邀请后自动发送 `.助阵`（无需 reply）
- 每日问安
  - 每 12 小时发送一次 `.每日问安`

### 4) 宗门日常（点卯 + 传功）
插件：`zongmen`
- 每天定时 `.宗门点卯`
- 每天定时传功 3 次：先发一条“心得”（可配置），再回复该消息发送 `.宗门传功`

## 日志与限流策略
- 默认 `LOG_LEVEL=INFO` 时，只输出两类关键信息：
  - `>>` 我方发送的指令
  - `<<` 系统回包（仅“高置信相关”的消息）
- 发送限流：
  - `GLOBAL_SENDS_PER_MINUTE`（全局）
  - `PLUGIN_SENDS_PER_MINUTE`（每插件）
  - 当触发限流时，不会丢指令：会自动等待到允许发送的时间后重试，直到发出为止。

## 快速开始

### 1) 安装依赖
- Python >= 3.10
- 安装 Telethon：
```bash
python3 -m pip install telethon
```

（可选）使用虚拟环境：
```bash
python3 -m venv venv
source venv/bin/activate
python3 -m pip install telethon
```

### 2) 配置
复制示例配置并填写必要参数：
```bash
cp .env.example .env
```

你至少需要设置：
- `TG_API_ID` / `TG_API_HASH`（从 https://my.telegram.org 获取）
- `GAME_CHAT_ID`（群组 id）
- `TOPIC_ID`（如果群是论坛话题：话题 root message id）
- `MY_NAME`（你在游戏里的名字，用于过滤专属回包）

然后按需打开插件：
- `ENABLE_BIGUAN=1`
- `ENABLE_GARDEN=1`
- `ENABLE_XINGGONG=1`
- `ENABLE_ZONGMEN=1`

建议第一次先用 `DRY_RUN=1` 观察日志确认无误后再改为 `0`。

### 3) 运行
从项目根目录启动：
```bash
python3 xiuxian.py
```

首次运行会进行 Telegram 登录并生成本地会话文件（`.session`）。

## 作为 systemd 服务运行（可选）
下面是 user service 示例（更推荐，不需要 root）：

1) 创建：`~/.config/systemd/user/xiuxian-helper.service`
```ini
[Unit]
Description=Xiuxian Helper
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/xiuxian
ExecStart=/path/to/xiuxian/venv/bin/python /path/to/xiuxian/xiuxian.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

2) 启动：
```bash
systemctl --user daemon-reload
systemctl --user enable --now xiuxian-helper.service
journalctl --user -u xiuxian-helper.service -f
```

如果你不使用 venv，把 `ExecStart` 的 python 路径改成 `/usr/bin/python3` 即可。

---

如需扩展新玩法，推荐新增一个 `xiuxian_bot/plugins/<feature>.py` 插件，并实现 `on_message()`（观察回包 -> 产出 SendAction），配合 `Scheduler` 做定时任务。

