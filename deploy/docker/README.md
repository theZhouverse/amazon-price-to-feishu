# Docker 部署与跨设备迁移

Docker 是价格任务的可选部署方式；Windows 任务计划和 Docker 容器只能二选一，不能同时调度同一份结果表。当前镜像默认只运行价格抓取，HTML 归档和 HTML 服务均关闭，价格流程不等待 HTML。

## 镜像内容

- `Dockerfile`：Python 3.12、Chromium、DrissionPage 依赖、cron、tini。
- `docker-compose.yml`：持久化 `outputs`、`data`、`htmls`，运行时注入 Secret，提供 cron 健康检查。
- `entrypoint.sh`：启动配置自检，写入 `/etc/cron.d/amazon-daily`，前台运行 cron。

容器 cron 使用系统格式（包含 `root` 用户列），不再调用 `crontab` 导入，避免 7 列系统任务被当成 5 列用户任务而失效。

## 本机预检

```powershell
docker version
docker compose version
docker compose -f deploy/docker/docker-compose.yml config
docker compose -f deploy/docker/docker-compose.yml build --pull
```

已在当前开发机的 Docker Desktop Linux 引擎完成一次真实构建、容器内 Chromium/编译、269 项回归和短启动健康检查；入口成功加载18个子表并达到 `healthy`，随后容器已停止，未触发抓取、飞书写入或通知。迁移到另一台机器后仍必须重新执行以上命令，并按该机器的网络出口完成 US/CA 只读验证；当前机器的成功不代表云端出口不会触发 Amazon 风控。

## 跨设备迁移

1. 在旧机器确认没有运行中的 `weekly_scheduler.lock`，先停用 Windows 任务或旧容器，避免两台设备同时写固定结果表。
2. 复制代码仓库（不要复制 `.venv`、`.git`、缓存或临时目录）。
3. 复制需要持续保留的运行数据到新机器的 `deploy/docker/volumes/`：
   - `outputs/`：weekly manifest、snapshot 指针、daily bundle、delivery、通知回执、日志、备份；
   - `data/`：业务运行数据；
   - `htmls/`：仅在启用 HTML 时复制，价格任务默认不使用。
4. 复制 `config/config.json`，检查其中只包含非敏感配置。新机器如启用 HTML，应把 `html_archive_root` 或环境变量 `AMAZON_HTML_ARCHIVE_ROOT` 设置为 `/app/htmls`，不能保留 Windows `D:\...` 路径。
5. 通过安全方式设置 `FS_APP_SECRET`，不要写入镜像、Git、compose 文件或日志。可用同目录 `.env`，但不提交该文件。
6. 在新机器执行：

   ```bash
   docker compose -f deploy/docker/docker-compose.yml config
   docker compose -f deploy/docker/docker-compose.yml up -d --build
   docker compose -f deploy/docker/docker-compose.yml ps
   docker compose -f deploy/docker/docker-compose.yml logs --tail=100 amazon-daily
   ```

7. 首次只读验收：

   ```bash
   docker compose -f deploy/docker/docker-compose.yml exec amazon-daily \
     python app/main.py --inspect-weekly-registry
   docker compose -f deploy/docker/docker-compose.yml exec amazon-daily \
     python app/main.py --weekly-run --dry-run --limit 1
   ```

   验收源登记、当前快照、Amazon 出口、CA/US 币种和日志后，才允许执行 `--weekly-run --confirm`。

## 迁移边界与风险

- 容器出口 IP 是新机器的出口 IP；云服务器数据中心 IP 可能触发 Amazon 验证码或地区拦截，Docker 不会自动换 IP。需要先验证相同 VPN/代理出口和 CA 邮编。
- Chromium/DrissionPage 版本、代理、系统时区和字体会影响解析结果；镜像固定 Python 依赖，但 Amazon 风控仍属于外部变量。
- `outputs/` 必须持久化，否则会丢失当前周期 manifest、恢复缓存、结果备份、通知幂等账本，可能触发旧周期保护或重复发送。
- 迁移后只能保留一套调度器。若改用 Docker cron，应禁用 `AmazonDaily_0730`/`AmazonDaily_1530`；回滚到 Windows 时先停容器再启用任务。
- 固定结果表、源登记表和应用 Secret 不随镜像迁移；它们由配置与运行时凭据决定。

## 回滚

```bash
docker compose -f deploy/docker/docker-compose.yml down
```

保留 `deploy/docker/volumes/`，恢复旧机器或另一份已验证镜像后再启动。不要删除 `outputs/weekly_runs`、`daily_runs`、`target_backups` 或通知回执；这些是审计和恢复依据。
