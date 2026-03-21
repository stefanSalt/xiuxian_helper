# Docker 化改造计划（2026-03-21）

## 目标
- 为当前多账户 Web 版 `xiuxian_helper` 提供可部署的 Docker 运行方案。
- 保持现有 Python 入口与运行逻辑不变，优先做最小侵入式改造。
- 明确持久化目录，避免 Telethon session、sqlite 数据库、日志在容器重建后丢失。

## 已识别的关键点
- 启动入口：`python xiuxian.py`
- Web 服务：FastAPI + uvicorn，监听 `WEB_HOST/WEB_PORT`
- 持久化数据：
  - `.env`
  - `APP_DB_PATH` 指向的 sqlite
  - `LOG_DIR`
  - Telethon `.session` 文件
- 依赖：`telethon / fastapi / uvicorn / jinja2 / python-multipart`

## 计划步骤
- [ ] 确认 Docker 运行边界（单容器 / compose / 数据卷 / 端口）
- [ ] 新增 `.dockerignore`
- [ ] 新增 `Dockerfile`
- [ ] 视确认结果新增 `docker-compose.yml`
- [ ] 调整 `.env.example` / `README.md` 的 Docker 启动说明
- [ ] 本地执行最小验证（镜像构建/配置检查）

## 已确认
1. 交付形式
- [x] a. `Dockerfile + docker-compose.yml`

2. 持久化方式
- [x] a. 挂载宿主目录保存 `.env / sqlite / logs / *.session`

3. 端口暴露
- [x] b. 改成 `11111`

4. 镜像基础
- [x] a. `python:3.12-slim`

## 执行进度
- [x] 新增 `.dockerignore`
- [x] 新增 `Dockerfile`
- [x] 新增 `docker-compose.yml`
- [x] 新增/调整会话持久化路径配置，避免 `.session` 落到容器临时层
- [x] 更新 `.env.example` / `README.md`
- [x] 进行本地最小验证
- [x] 增加 Web 健康检查端点与容器 healthcheck
- [x] 验证容器健康状态

## 验证结果
- [x] `python3 -m unittest` 通过：`83 passed / 4 skipped`
- [x] `./venv/bin/python -m unittest` 通过：`83 passed`
- [x] `docker compose config` 通过
- [x] `docker compose build` 通过
- [x] 临时容器健康检查通过：`/healthz -> 200`，容器状态 `healthy`
