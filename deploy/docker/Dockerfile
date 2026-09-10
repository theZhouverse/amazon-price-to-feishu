# syntax=docker/dockerfile:1
# Amazon Daily — reproducible price-only runtime image.
# 构建：docker build -f deploy/docker/Dockerfile -t amazon-daily:latest .
FROM python:3.12-slim

# 时区：正式任务按北京时间工作日 07:30 / 15:30 调度
ENV TZ=Asia/Shanghai     PYTHONUNBUFFERED=1     PYTHONIOENCODING=utf-8     PYTHONPATH=/app/app

# DrissionPage 需要真实 Chromium；不得在镜像里固化任何凭据
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium ca-certificates fonts-liberation cron procps tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖层缓存
COPY config/requirements.txt config/requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    -r config/requirements.txt

# 源码（排除 outputs/.venv/.git/htmls/data 等，见 .gitignore）
COPY app ./app
COPY config ./config
COPY bin ./bin

# 运行数据目录由 compose 卷覆盖并持久化
RUN mkdir -p /app/outputs/logs /app/data /app/tmp /app/htmls

# 启动：校验配置后注册 cron 并前台运行 cron
COPY deploy/docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["cron"]
