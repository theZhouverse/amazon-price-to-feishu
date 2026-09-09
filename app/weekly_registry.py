# -*- coding: utf-8 -*-
"""固定周报登记表的纯解析与确定性选择规则。"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

STANDARD_HEADERS = ('period_id', 'source_url', 'effective_at', 'status')
SIMPLE_HEADERS = ('序号', '飞书链接', '更新时间')
ALLOWED_RESOURCE_TYPES = ('wiki', 'sheets')


@dataclass(frozen=True)
class RegistrySelection:
    row_number: int
    period_id: str
    source_url: str
    effective_at: datetime | None
    status: str
    raw: dict
    sequence: int | None = None
    scheduled_slot: str = 'manual'
    selection_mode: str = 'manual'
    pending_period_change: dict | None = None


def validate_feishu_resource_url(url: str, allowed_hosts: list[str]) -> tuple[str, str]:
    """校验飞书域名与资源类型，返回 (resource_type, token)。"""
    parsed = urlparse(str(url).strip())
    host = (parsed.hostname or '').lower()
    allowed = {str(item).strip().lower() for item in allowed_hosts if str(item).strip()}
    if parsed.scheme != 'https' or host not in allowed:
        raise RuntimeError(f'飞书链接域名不允许: {host or "<empty>"}')
    parts = [part for part in parsed.path.split('/') if part]
    if len(parts) < 2 or parts[0] not in ALLOWED_RESOURCE_TYPES:
        raise RuntimeError('飞书链接只允许 /wiki/{token} 或 /sheets/{token}')
    token = parts[1]
    if not token or not all(ch.isalnum() or ch in '_-' for ch in token):
        raise RuntimeError('飞书资源 Token 格式错误')
    return parts[0], token


def _parse_datetime(value, tz=timezone(timedelta(hours=8))) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or '').strip()
        if not text:
            raise RuntimeError('effective_at 不能为空')
        normalized = text.replace('Z', '+00:00').replace('/', '-')
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise RuntimeError(f'effective_at 格式错误: {text}') from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def _cell_link(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get('link') or '').strip()
    if isinstance(value, list):
        for part in value:
            link = _cell_link(part)
            if link:
                return link
    return ''


def parse_registry_values(values: list[list]) -> list[dict]:
    """识别标准控制表或用户的三列表，并转换为统一记录。"""
    matches: list[tuple[int, str, dict[str, int]]] = []
    for row_index, row in enumerate(values[:20]):
        cells = [str(cell or '').strip() for cell in row]
        normalized = [cell.lower() for cell in cells]
        standard = {name: normalized.index(name) for name in STANDARD_HEADERS if name in normalized}
        simple = {name: cells.index(name) for name in SIMPLE_HEADERS if name in cells}
        if len(standard) == len(STANDARD_HEADERS):
            matches.append((row_index, 'standard', standard))
        if len(simple) == len(SIMPLE_HEADERS):
            matches.append((row_index, 'simple', simple))
    if len(matches) != 1:
        raise RuntimeError(f'登记表必须有且仅有一行标准表头，当前匹配 {len(matches)} 行')

    header_index, schema, positions = matches[0]
    header = [str(cell or '').strip() for cell in values[header_index]]
    records: list[dict] = []
    for offset, row in enumerate(values[header_index + 1:], start=header_index + 2):
        if not any(str(cell or '').strip() for cell in row):
            continue
        record = {
            name: (row[index] if index < len(row) else '')
            for index, name in enumerate(header) if name
        }
        record['_row_number'] = offset
        if schema == 'simple':
            sequence = row[positions['序号']] if positions['序号'] < len(row) else ''
            link_cell = row[positions['飞书链接']] if positions['飞书链接'] < len(row) else ''
            updated = row[positions['更新时间']] if positions['更新时间'] < len(row) else ''
            record.update({
                '_schema': 'simple', 'sequence': sequence,
                'period_id': f'seq-{sequence}', 'source_url': _cell_link(link_cell),
                'updated_at': updated, 'effective_at': '', 'status': 'active',
            })
        else:
            for required, index in positions.items():
                record[required] = row[index] if index < len(row) else ''
            record['_schema'] = 'standard'
        records.append(record)
    return records


def select_current_registry_row(records: list[dict], now: datetime,
                                allowed_hosts: list[str]) -> RegistrySelection:
    """按 SPEC 16.6 选择唯一当前登记行，不回退到更旧的故障行。"""
    if now.tzinfo is None:
        raise RuntimeError('now 必须包含时区')
    now_utc = now.astimezone(timezone.utc)

    if records and all(record.get('_schema') == 'simple' for record in records):
        parsed_simple = []
        seen = set()
        for record in records:
            if not str(record.get('source_url') or '').strip():
                continue
            try:
                sequence = int(record.get('sequence'))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f'登记表第 {record.get("_row_number")} 行序号必须是整数') from exc
            if sequence in seen:
                raise RuntimeError(f'序号重复: {sequence}')
            seen.add(sequence)
            parsed_simple.append((sequence, record))
        if not parsed_simple:
            raise RuntimeError('登记表不存在飞书链接非空的有效行')
        parsed_simple.sort(key=lambda item: item[0], reverse=True)
        sequence, record = parsed_simple[0]
        source_url = str(record.get('source_url') or '').strip()
        validate_feishu_resource_url(source_url, allowed_hosts)
        return RegistrySelection(
            row_number=int(record.get('_row_number') or 0),
            period_id=f'seq-{sequence}', source_url=source_url,
            effective_at=None, status='active', raw=dict(record), sequence=sequence,
        )

    period_counts: dict[str, int] = {}
    parsed: list[tuple[dict, datetime]] = []
    for record in records:
        period_id = str(record.get('period_id') or '').strip()
        if not period_id:
            raise RuntimeError(f'登记表第 {record.get("_row_number")} 行 period_id 为空')
        period_counts[period_id] = period_counts.get(period_id, 0) + 1
        effective = _parse_datetime(record.get('effective_at'))
        parsed.append((record, effective))
    duplicates = sorted(key for key, count in period_counts.items() if count > 1)
    if duplicates:
        raise RuntimeError(f'period_id 重复: {", ".join(duplicates)}')

    eligible = [item for item in parsed
                if str(item[0].get('status') or '').strip().lower() == 'active'
                and item[1] <= now_utc]
    if not eligible:
        raise RuntimeError('登记表不存在有效且已生效的 active 行')
    eligible.sort(key=lambda item: item[1], reverse=True)
    latest_time = eligible[0][1]
    latest = [item for item in eligible if item[1] == latest_time]
    if len(latest) != 1:
        raise RuntimeError('最大 effective_at 并列，无法确定唯一当前周报')

    record, effective = latest[0]
    source_url = str(record.get('source_url') or '').strip()
    validate_feishu_resource_url(source_url, allowed_hosts)
    return RegistrySelection(
        row_number=int(record.get('_row_number') or 0),
        period_id=str(record['period_id']).strip(),
        source_url=source_url,
        effective_at=effective,
        status='active',
        raw=dict(record),
    )


def _selection_from_record(record: dict, allowed_hosts: list[str]) -> RegistrySelection:
    """Build a validated selection for a specific already-known period."""
    source_url = str(record.get('source_url') or '').strip()
    if not source_url:
        raise RuntimeError(f'登记表第 {record.get("_row_number")} 行链接为空')
    validate_feishu_resource_url(source_url, allowed_hosts)
    if record.get('_schema') == 'simple':
        try:
            sequence = int(record.get('sequence'))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f'登记表第 {record.get("_row_number")} 行序号必须是整数') from exc
        return RegistrySelection(
            row_number=int(record.get('_row_number') or 0),
            period_id=f'seq-{sequence}', source_url=source_url,
            effective_at=None, status='active', raw=dict(record), sequence=sequence,
        )
    effective = _parse_datetime(record.get('effective_at'))
    if str(record.get('status') or '').strip().lower() != 'active':
        raise RuntimeError(f'登记表周期不是 active: {record.get("period_id")}')
    return RegistrySelection(
        row_number=int(record.get('_row_number') or 0),
        period_id=str(record.get('period_id') or '').strip(), source_url=source_url,
        effective_at=effective, status='active', raw=dict(record),
    )


def _find_period_selection(records: list[dict], period_id: str,
                           allowed_hosts: list[str]) -> RegistrySelection:
    matches = []
    for record in records:
        candidate = str(record.get('period_id') or '').strip()
        if candidate == str(period_id or '').strip():
            matches.append(record)
    if len(matches) != 1:
        raise RuntimeError(f'上一周期 {period_id!r} 在登记表中不存在唯一有效记录')
    return _selection_from_record(matches[0], allowed_hosts)


def _is_newer_selection(current: RegistrySelection, previous: RegistrySelection) -> bool:
    if current.sequence is not None and previous.sequence is not None:
        return current.sequence > previous.sequence
    if current.effective_at is not None and previous.effective_at is not None:
        return current.effective_at > previous.effective_at
    if current.period_id == previous.period_id:
        return False
    raise RuntimeError('相邻周期缺少可比较的序号或生效时间，禁止静默切换')


def select_for_scheduled_slot(records: list[dict], now: datetime,
                              allowed_hosts: list[str], scheduled_slot: str,
                              previous_period_id: str = '') -> RegistrySelection:
    """Select a source period using the scheduler's planned slot.

    ``StartWhenAvailable`` can launch a Monday task hours late.  The slot is
    therefore an explicit input, not inferred from the process wall clock.
    Monday morning carries the prior fixed period, Monday afternoon requires a
    newer registry row, and weekday slots keep the prior period while exposing
    a pending change.
    """
    slot = str(scheduled_slot or 'manual').strip().lower()
    allowed = {'manual', 'monday_0730', 'monday_1530',
               'weekday_0730', 'weekday_1530'}
    if slot not in allowed:
        raise RuntimeError(f'不支持的 scheduled_slot: {scheduled_slot!r}')
    latest = select_current_registry_row(records, now, allowed_hosts)
    previous = (_find_period_selection(records, previous_period_id, allowed_hosts)
                if previous_period_id else None)

    if slot == 'manual':
        return replace(latest, scheduled_slot=slot, selection_mode='manual')
    if slot == 'monday_0730':
        if previous is None:
            raise RuntimeError('周一07:30缺少上一周期固定manifest/period_id，安全停止')
        return replace(previous, scheduled_slot=slot, selection_mode='monday_carryover')
    if slot == 'monday_1530':
        if previous is not None and not _is_newer_selection(latest, previous):
            raise RuntimeError('周一15:30登记表没有比上一周期更高的有效序号，安全停止')
        return replace(latest, scheduled_slot=slot, selection_mode='monday_switch')

    # Tuesday-Friday must not silently adopt a newly appeared row.  Keep the
    # ready period and carry an auditable pending change to the manifest/log.
    if previous is None:
        raise RuntimeError('工作日稳态任务缺少上一周期固定manifest/period_id，安全停止')
    pending = None
    if latest.period_id != previous.period_id:
        pending = {
            'period_id': latest.period_id,
            'source_url': latest.source_url,
            'row_number': latest.row_number,
            'reason': '非换周时点发现更高有效登记，不自动切换',
        }
    return replace(previous, scheduled_slot=slot, selection_mode='weekday_steady',
                   pending_period_change=pending)
