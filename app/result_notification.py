"""Shared completion-message formatting; no filesystem or network side effects."""
from __future__ import annotations

from datetime import datetime

INSTRUCTIONS_URL = 'https://wit0jhu6kvu.feishu.cn/wiki/G531wP7WNiepV3krnrHcavqin6d'
_COMPLETION_PREFIXES = (
    'Hi，有个 Amazon 周报前端价格捕捉任务完成，请查收；',
    # Keep historical hand-written/recovery messages renderable while all
    # production completion_text calls use the new wording above.
    'Hi，有个任务完成请查收.',
)


def display_period(period_id: str, run_id: str) -> str:
    """Human week label derived from the immutable run date.

    The registry sequence remains in manifests for identity/audit, while users
    see the actual ISO week and are no longer left with a misleading seq-2.
    """
    try:
        day = datetime.strptime(str(run_id)[:8], '%Y%m%d').date()
        year, week, _ = day.isocalendar()
        return f'{year}-W{week:02d}（登记序号 {period_id}）'
    except (TypeError, ValueError):
        return str(period_id)


def result_title(period_id: str, run_id: str) -> str:
    week = display_period(period_id, run_id).split('（', 1)[0]
    return f'Amazon周报前端价格捕捉_{week}_{run_id}'


def completion_text(*, period_id: str, run_id: str, started_at: str,
                    finished_at: str, elapsed_seconds: float, sheet_count: int,
                    written_rows: int, blocked_count: int, result_name: str,
                    result_url: str, local_data: str,
                    error_ratio: float | None = None) -> str:
    lines = [
        'Hi，有个 Amazon 周报前端价格捕捉任务完成，请查收；',
        '',
        f'周期：{display_period(period_id, run_id)}', f'运行编号：{run_id}',
        f'开始：{started_at.replace("T", " ")}', f'结束：{finished_at.replace("T", " ")}',
        f'完整耗时：{int(elapsed_seconds) // 60}分{int(elapsed_seconds) % 60:02d}秒',
        '',
        f'子表数：{sheet_count}', f'写入行：{written_rows}',
        f'阻断行：{blocked_count}',
    ]
    if error_ratio is not None:
        lines.append(f'技术异常率：{error_ratio:.1%}')
    lines.extend(['', f'结果表：{result_name}', result_url,
                  f'本地数据：{local_data}', f'说明文档：{INSTRUCTIONS_URL}'])
    return '\n'.join(lines)


def _compact_completion_text(text: str) -> str:
    """Return the collaborator-safe completion variant.

    The manager receives operational timings and the local evidence path.  All
    other application collaborators receive only the result-table and fixed
    instructions links, so private paths and run statistics are not disclosed.
    Non-completion/error text is returned unchanged for backwards compatibility.
    """
    lines = text.splitlines()
    if not lines or not lines[0].startswith(_COMPLETION_PREFIXES[0]):
        return text
    result_index = next((i for i, line in enumerate(lines)
                         if line.startswith('结果表：')), None)
    instruction_index = next((i for i, line in enumerate(lines)
                              if line.startswith('说明文档：')), None)
    if result_index is None or instruction_index is None:
        # Fail closed if a malformed completion body cannot be reduced safely.
        return '\n'.join([lines[0], '', '结果表：', '说明文档：'])
    compact = [lines[0], '']
    compact.append(lines[result_index])
    if result_index + 1 < len(lines) and lines[result_index + 1].startswith('https://'):
        compact.append(lines[result_index + 1])
    compact.extend(['', lines[instruction_index]])
    return '\n'.join(compact)


def recipient_text(text: str, open_id: str, local_data_open_id: str = '') -> str:
    """Select the manager-full or collaborator-compact completion template."""
    if local_data_open_id and open_id == local_data_open_id:
        return text
    if text.startswith(_COMPLETION_PREFIXES[0]):
        return _compact_completion_text(text)
    return '\n'.join(line for line in text.splitlines()
                     if not line.lstrip().startswith(('本地数据:', '本地数据：')))


def completion_post(text: str) -> dict:
    """Render named links and spacing without duplicate URL previews."""
    lines = text.splitlines()
    content = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith('结果表：') and index + 1 < len(lines) and lines[index + 1].startswith('https://'):
            content.append([{'tag': 'text', 'text': '结果表：'},
                            {'tag': 'a', 'text': line.split('：', 1)[1], 'href': lines[index + 1]}])
            index += 2
            continue
        if line.startswith('说明文档：'):
            content.append([{'tag': 'text', 'text': '说明文档：'},
                            {'tag': 'a', 'text': '关于上述表格的简要说明', 'href': INSTRUCTIONS_URL}])
        else:
            content.append([{'tag': 'text', 'text': line}])
        index += 1
    return {'zh_cn': {'title': '', 'content': content}}


def send_to_recipients(fc, recipients: list[str], text: str, *, local_data_open_id: str = '', checkpoint=None) -> dict:
    """One failed recipient must not prevent delivery to the remaining recipients."""
    report = {'sent': [], 'failed': []}
    for open_id in dict.fromkeys(x for x in recipients if x):
        try:
            body = recipient_text(text, open_id, local_data_open_id)
            if body.startswith(_COMPLETION_PREFIXES):
                message_id = fc.send_post_message(open_id, completion_post(body))
            else:
                message_id = fc.send_text_message(open_id, body)
            report['sent'].append({'open_id': open_id, 'message_id': message_id})
        except Exception as exc:
            report['failed'].append({'open_id': open_id, 'error': str(exc)[:500]})
        if checkpoint is not None:
            checkpoint(report)
    return report
