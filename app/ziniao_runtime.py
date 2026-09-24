# -*- coding: utf-8 -*-
"""Amazon Daily's adapter for the shared Ziniao/ZClaw control plane.

This module deliberately contains no credentials and no project-local copy of
the Purple Bird CLI, Bridge, store IDs, browser profiles, or authentication
state.  It reads the central, non-secret runtime registry and exposes only the
minimum information needed by the Feedback browser boundary.

The adapter is intentionally fail-closed: a missing/unknown project, host,
CLI entrypoint, store registration, or shared lock is a blocked run rather
than a reason to fall back to an old local configuration.
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


CONTROL_ROOT = Path(r"D:\projects\lykj-projects-map")
RUNTIME_CONFIG_PATH = CONTROL_ROOT / "config" / "ziniao-runtime.json"
REGISTRY_PATH = CONTROL_ROOT / "config" / "automation-registry.json"
GLOBAL_LOCK_PATH = Path(r"D:\projects.runtime\ziniao\_sellercentral.lock")
CONTEXT_STATUS_PATH = Path(r"D:\projects.runtime\ziniao\_context\_status.json")
GLOBAL_CLI_WRAPPER = CONTROL_ROOT / "scripts" / "ziniao-prod.ps1"
PROJECT_ID = "amazon_daily"


class ZiniaoRuntimeError(RuntimeError):
    """The shared runtime is unavailable or inconsistent."""


class ZiniaoRuntimeBusy(ZiniaoRuntimeError):
    """Another project currently owns the shared Seller Central lock."""


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ZiniaoRuntimeError(f"全局紫鸟运行时文件不存在: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ZiniaoRuntimeError(f"全局紫鸟运行时文件无法读取: {path}") from exc
    if not isinstance(data, dict):
        raise ZiniaoRuntimeError(f"全局紫鸟运行时文件不是对象: {path}")
    return data


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _same_path(left: object, right: Path) -> bool:
    try:
        return Path(str(left)).resolve().as_posix().lower() == right.resolve().as_posix().lower()
    except (OSError, RuntimeError):
        return str(left).replace("/", "\\").rstrip("\\").lower() == str(right).replace("/", "\\").rstrip("\\").lower()


def load_global_runtime(project_root: Path | None = None,
                        project_id: str = PROJECT_ID) -> dict:
    """Load and validate the central runtime for this project.

    Only non-secret metadata is returned.  The global files themselves must
    remain the source of truth; callers must not copy values into config.json.
    """
    runtime = _read_json(RUNTIME_CONFIG_PATH)
    registry = _read_json(REGISTRY_PATH)
    root = Path(project_root) if project_root else Path(__file__).resolve().parent.parent
    project = next((item for item in registry.get("projects", [])
                    if str(item.get("id") or "") == project_id), None)
    if not isinstance(project, dict):
        raise ZiniaoRuntimeError(f"项目未登记到全局注册表: {project_id}")
    if not _same_path(project.get("root"), root):
        raise ZiniaoRuntimeError(
            f"全局注册表项目根目录不匹配: expected={root} actual={project.get('root')}")

    security = runtime.get("security") or {}
    lock_path = Path(str(security.get("global_lock_path") or GLOBAL_LOCK_PATH))
    status_path = Path(str(security.get("context_status_path") or CONTEXT_STATUS_PATH))
    if not _same_path(lock_path, GLOBAL_LOCK_PATH):
        raise ZiniaoRuntimeError(
            f"全局锁路径不是固定路径: {lock_path} != {GLOBAL_LOCK_PATH}")
    if not _same_path(status_path, CONTEXT_STATUS_PATH):
        raise ZiniaoRuntimeError(
            f"全局上下文状态路径不是固定路径: {status_path} != {CONTEXT_STATUS_PATH}")

    default_host = str(runtime.get("default_host_id") or "").strip()
    host_id = str(os.environ.get("LYKJ_HOST_ID") or default_host).strip()
    host = next((item for item in runtime.get("hosts", [])
                 if str(item.get("id") or "") == host_id), None)
    if not isinstance(host, dict):
        raise ZiniaoRuntimeError(f"全局紫鸟主机未登记: {host_id or '<empty>'}")
    if str(host.get("status") or "") not in {"verified_read_only", "production_ready"}:
        raise ZiniaoRuntimeError(
            f"全局紫鸟主机未通过只读验收: {host_id} status={host.get('status')}")

    registered_ids = [str(item).strip() for item in (project.get("stores") or []) if str(item).strip()]
    scope_names = {project_id, str(project.get("label") or "").strip()}
    global_stores = [item for item in (runtime.get("stores") or [])
                     if scope_names.intersection(
                         {str(scope).strip() for scope in (item.get("scope") or [])})]
    global_stores.sort(key=lambda item: int(item.get("execution_order") or 9999))
    global_ids = [str(item.get("store_id") or "").strip() for item in global_stores]
    if not global_stores or any(not item for item in global_ids):
        raise ZiniaoRuntimeError(f"全局运行时未登记项目店铺: {project_id}")
    if registered_ids and registered_ids != global_ids:
        raise ZiniaoRuntimeError(
            f"注册表与运行时店铺范围不一致: registry={registered_ids} runtime={global_ids}")

    return {
        "project_id": project_id,
        "project": project,
        "runtime": runtime,
        "registry": registry,
        "host_id": host_id,
        "host": host,
        "stores": global_stores,
        "store_ids": global_ids,
        "lock_path": GLOBAL_LOCK_PATH,
        "status_path": CONTEXT_STATUS_PATH,
        "cli_wrapper": GLOBAL_CLI_WRAPPER,
    }


def resolve_cli_wrapper(project_root: Path | None = None) -> Path:
    """Return the central wrapper; it resolves the host-specific CLI itself."""
    info = load_global_runtime(project_root)
    wrapper = Path(info["cli_wrapper"])
    if not wrapper.is_file():
        raise ZiniaoRuntimeError(f"全局紫鸟 CLI 入口不存在: {wrapper}")
    return wrapper


def resolve_feedback_stores(feedback_cfg: dict, *, project_root: Path | None = None,
                            project_id: str = PROJECT_ID) -> dict:
    """Merge project selectors with globally registered store identities.

    Selectors and page URLs are project business adapters.  Store IDs, names,
    platform and execution order belong to the central runtime and are never
    accepted from the project config.  The returned object is in-memory only.
    """
    info = load_global_runtime(project_root, project_id)
    local_stores = feedback_cfg.get("stores") or []
    if len(local_stores) != len(info["stores"]):
        raise ZiniaoRuntimeError(
            f"项目 Feedback 适配器数量与全局店铺数量不一致: {len(local_stores)} != {len(info['stores'])}")
    resolved = []
    seen_keys: set[str] = set()
    forbidden = ("store_id", "expected_store_identity", "display_name", "secret_ref",
                 "profile", "profile_path", "cookie", "credential", "api_key", "token")
    for index, local in enumerate(local_stores):
        if not isinstance(local, dict):
            raise ZiniaoRuntimeError("项目 Feedback 店铺适配器必须是对象")
        key = str(local.get("key") or "").strip()
        if not key or key in seen_keys:
            raise ZiniaoRuntimeError("项目 Feedback 店铺 key 为空或重复")
        seen_keys.add(key)
        duplicate = [name for name in forbidden if str(local.get(name) or "").strip()]
        if duplicate:
            raise ZiniaoRuntimeError(
                "项目 config 不得重复保存全局店铺/认证字段: " + ", ".join(duplicate))
        global_store = info["stores"][index]
        item = dict(local)
        item.update({
            "store_id": str(global_store.get("store_id") or "").strip(),
            "expected_store_identity": str(global_store.get("store_id") or "").strip(),
            "display_name": str(global_store.get("store_name") or "").strip(),
            "identity_mode": "cli_store_context",
            "global_store_source": str(RUNTIME_CONFIG_PATH),
        })
        resolved.append(item)
    merged = dict(feedback_cfg)
    merged["stores"] = resolved
    merged["store_order"] = [item["key"] for item in resolved]
    merged["ziniao_runtime_project_id"] = project_id
    merged["ziniao_runtime_host_id"] = info["host_id"]
    merged["ziniao_runtime_store_ids"] = list(info["store_ids"])
    return merged


def _write_status(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def global_sellercentral_lock(*, phase: str = "feedback", store_ids: list[str] | None = None,
                              project_root: Path | None = None,
                              project_id: str = PROJECT_ID,
                              lock_path: Path | None = None,
                              status_path: Path | None = None) -> Iterator[dict]:
    """Hold the shared store lifecycle lock and publish safe context status."""
    info = load_global_runtime(project_root, project_id)
    lock_path = Path(lock_path) if lock_path else Path(info["lock_path"])
    status_path = Path(status_path) if status_path else Path(info["status_path"])
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = lock_path.open("a+b")
    except OSError as exc:
        raise ZiniaoRuntimeError(f"全局紫鸟锁不可用: {lock_path}") from exc
    locked = False
    started = _utc_now()
    status = {
        "schema_version": "2026-09-23.v1",
        "status": "active",
        "project_id": project_id,
        "phase": phase,
        "host_id": info["host_id"],
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "store_ids": [str(item) for item in (store_ids or info["store_ids"])],
        "started_at": started,
        "updated_at": started,
    }
    try:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise ZiniaoRuntimeBusy(
                f"全局紫鸟店铺锁已被占用，本次不打开店铺: {lock_path}") from exc
        _write_status(status_path, status)
        yield info
    finally:
        if locked:
            released = dict(status)
            released.update({"status": "released", "updated_at": _utc_now(),
                             "released_at": _utc_now()})
            try:
                _write_status(status_path, released)
            except OSError:
                # Lock release remains the priority; diagnostics are best effort.
                pass
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        else:
            handle.close()
