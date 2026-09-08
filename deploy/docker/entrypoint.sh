#!/usr/bin/env bash
set -e

mkdir -p /app/outputs/logs /app/data /app/tmp /app/htmls

# 1) 启动自检：仅校验配置可加载（不触发网络/飞书写入）
echo "[entrypoint] checking config..."
python -c "from config import load_config; cfg=load_config(); print('[ok] config loaded, sheets=%d' % len(cfg['sheets']))"

# 2) 注册 cron（容器内时区已为 Asia/Shanghai，cron 按当地时间执行）
cat > /etc/cron.d/amazon-daily <<'EOF'
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
PYTHONPATH=/app/app
# 周一至周五 07:30 / 15:30 北京时间正式全量
30 7 * * 1-5 root cd /app && PYTHONPATH=/app/app /usr/local/bin/python app/main.py --weekly-run --confirm >> /app/outputs/logs/cron.log 2>&1
30 15 * * 1-5 root cd /app && PYTHONPATH=/app/app /usr/local/bin/python app/main.py --weekly-run --confirm >> /app/outputs/logs/cron.log 2>&1
EOF
chmod 0644 /etc/cron.d/amazon-daily
# Files under /etc/cron.d use the system-cron format (including the `root`
# user column).  Do not pass this file to `crontab`, whose per-user format has
# no user column and would silently reject the schedule.
touch /app/outputs/logs/cron.log

# 3) 前台运行 cron
exec cron -f
