# xiuxian_helper

一个运行在 Telegram 群组/话题里的修仙文字游戏自动化助手。

当前版本已经从“单账号 + 单份 `.env`”升级为：
- **多账号并行运行**
- **Web 管理后台**
- **账号配置存 sqlite**
- **插件状态持久化恢复**

Web 端采用 **FastAPI + 服务端渲染页面**，现阶段支持：
- 账号新增 / 编辑 / 删除
- 账号启用 / 停用
- 账号手动启动 / 停止
- 插件参数网页配置
- 每个账号最近日志查看

## 功能概览

每个账号仍然复用原来的插件体系，当前主要能力：
- 自动闭关
- 自动种植（小药园）
- 星宫（观星台、启阵、助阵、每日问安、深度闭关联动、观星劫持）
- 元婴（日常探寻裂缝、元婴出窍）
- 每日闯塔
- 宗门（日常点卯、传功）

## 目录与存储

- `.env`：**仅系统级配置**
- `APP_DB_PATH` 指向的 sqlite：账户配置 + 插件状态
- `logs/account_<id>.log`：每个账号独立日志

## 安装依赖

建议使用虚拟环境：

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

依赖包括：
- `telethon`
- `fastapi`
- `uvicorn`
- `jinja2`
- `python-multipart`

## 配置

先复制示例文件：

```bash
cp .env.example .env
```

首期只需要先填写系统级字段：
- `APP_DB_PATH`
- `LOG_DIR`
- `SESSION_ROOT_DIR`（可选）
- `WEB_HOST`
- `WEB_PORT`
- `WEB_ADMIN_USERNAME`
- `WEB_ADMIN_PASSWORD`
- `WEB_SECRET_KEY`

### 旧版单账号迁移

如果你是从旧版升级，可以把旧的账号字段暂时保留在 `.env` 中。
程序启动时，如果 `accounts` 表还是空的，会自动把这套旧配置迁移为第一个账号。
迁移完成后，建议把账号配置改到网页里维护，并删除 `.env` 里的旧账号字段。

## 启动方式

从项目根目录启动：

```bash
python3 xiuxian.py
```

现在这个入口会直接启动 Web 服务，而不是旧版单账号 CLI。

默认访问地址：

```text
http://127.0.0.1:8000
```

登录后：
1. 新增账号
2. 填 Telegram 参数、群组参数、插件开关和各玩法参数
3. 保存后可选择启用
4. 在面板里启动/停止账号

### Session 文件路径说明

- 本地直接运行时，`tg_session_name` 仍可填写普通名称，例如 `bot_a`
- 若设置了 `SESSION_ROOT_DIR`，脚本会把**相对**的 `tg_session_name` 自动解析到该目录下
- 例如：
  - `SESSION_ROOT_DIR=/app/data/sessions`
  - `tg_session_name=bot_a`
  - 实际 session 文件会落到 `/app/data/sessions/bot_a.session`

这一步是为了让 Docker 场景下的 session 持久化更稳定，不要求你在网页里手动填绝对路径

## 日志策略

默认 `INFO` 级别下，日志只保留重点：
- `>>` 我方发送的指令
- `<<` 系统关键回应
- `WARNING`/`ERROR` 异常信息

这样仍然符合你之前的要求：尽量只看“我发出的指令 / 系统回包”。

## 发送可靠性

所有账号发送都走统一的可靠发送层：
- 全局最小发送间隔：`GLOBAL_SEND_MIN_INTERVAL_SECONDS`
- 全局每分钟上限：`GLOBAL_SENDS_PER_MINUTE`
- 单插件每分钟上限：`PLUGIN_SENDS_PER_MINUTE`
- Telegram 限流或临时失败时自动退避重试

非会员号建议把 `GLOBAL_SEND_MIN_INTERVAL_SECONDS` 保持在 `10` 或更大。

## 状态恢复

插件关键状态会持久化到 sqlite，重启后尽量恢复：
- 自动闭关下一次触发时间
- 小药园 / 观星台下一次轮询时间
- 星宫启阵冷却、深度闭关联动、观星劫持窗口
- 元婴探寻裂缝 / 元婴出窍时间点
- 闯塔当天进度
- 宗门点卯 / 传功当天进度

## Docker 部署

已提供：
- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`

默认 Docker 方案：
- 镜像基础：`python:3.12-slim`
- 时区：`Asia/Shanghai`
- Web 端口：`11111`
- 持久化目录：宿主机 `./data`
- `.env`：宿主机根目录 `.env` 挂载到容器 `/app/.env`

### 启动

```bash
docker compose up -d --build
```

访问：

```text
http://127.0.0.1:11111
```

### 持久化内容

`docker-compose.yml` 默认会持久化：
- `./data/xiuxian_app.sqlite3`
- `./data/logs/`
- `./data/sessions/*.session`
- `./.env`

### 停止

```bash
docker compose down
```

### 查看日志

```bash
docker compose logs -f
```

### 健康检查

镜像内已内置健康检查，请求：
- `GET /healthz`

查看状态：

```bash
docker compose ps
# 或
docker inspect --format='{{json .State.Health}}' xiuxian-helper
```

> 注意：容器里已强制注入 `WEB_HOST=0.0.0.0`、`WEB_PORT=11111`、`APP_DB_PATH=/app/data/xiuxian_app.sqlite3`、`LOG_DIR=/app/data/logs`、`SESSION_ROOT_DIR=/app/data/sessions`，因此不需要你在 `.env` 里再手动写一遍这些 Docker 专用路径。

## systemd 示例

推荐继续用 user service：

```ini
[Unit]
Description=Xiuxian Helper Web
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

启用：

```bash
systemctl --user daemon-reload
systemctl --user enable --now xiuxian-helper.service
journalctl --user -u xiuxian-helper.service -f
```

## 测试

```bash
python3 -m unittest
# 或在虚拟环境中跑完整依赖版本
./venv/bin/python -m unittest
```

## 说明

- 旧版单账号脚本入口仍然叫 `xiuxian.py`，但行为已经变成启动 Web 管理端。
- 账号级配置不再推荐长期保存在 `.env`。
- 如果你后续要扩成真正的多用户、多权限、多节点，再往 API + 前后端分离演进即可；当前版本先保持最小可用。
