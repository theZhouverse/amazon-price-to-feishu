# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone
import unittest

from weekly_registry import (
    parse_registry_values, select_current_registry_row,
    select_for_scheduled_slot,
    validate_feishu_resource_url,
)

HOSTS = ['wit0jhu6kvu.feishu.cn']
NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone(timedelta(hours=8)))


def row(period, url, effective, status='active', number=2):
    return {
        'period_id': period,
        'source_url': url,
        'effective_at': effective,
        'status': status,
        '_row_number': number,
    }


class TestRegistryUrl(unittest.TestCase):
    def test_accepts_wiki_and_sheets(self):
        self.assertEqual(validate_feishu_resource_url(
            'https://wit0jhu6kvu.feishu.cn/wiki/Abc_123', HOSTS),
            ('wiki', 'Abc_123'))
        self.assertEqual(validate_feishu_resource_url(
            'https://wit0jhu6kvu.feishu.cn/sheets/Xyz-789?sheet=abc', HOSTS),
            ('sheets', 'Xyz-789'))

    def test_rejects_domain_and_resource_type(self):
        with self.assertRaisesRegex(RuntimeError, '域名不允许'):
            validate_feishu_resource_url('https://evil.example/wiki/Abc', HOSTS)
        with self.assertRaisesRegex(RuntimeError, '只允许'):
            validate_feishu_resource_url(
                'https://wit0jhu6kvu.feishu.cn/docx/Abc', HOSTS)


class TestRegistryValues(unittest.TestCase):
    def test_finds_header_and_rows(self):
        values = [
            ['周报登记'],
            ['period_id', 'source_url', 'effective_at', 'status', 'notes'],
            ['2026-W34', 'https://wit0jhu6kvu.feishu.cn/sheets/Current',
             '2026-08-20 09:00:00+08:00', 'active', 'ok'],
        ]
        records = parse_registry_values(values)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['_row_number'], 3)
        self.assertEqual(records[0]['notes'], 'ok')

    def test_missing_or_duplicate_header_fails(self):
        with self.assertRaisesRegex(RuntimeError, '匹配 0'):
            parse_registry_values([['period_id', 'source_url']])
        header = ['period_id', 'source_url', 'effective_at', 'status']
        with self.assertRaisesRegex(RuntimeError, '匹配 2'):
            parse_registry_values([header, header])

    def test_simple_chinese_table_extracts_rich_link(self):
        values = [
            ['序号', '飞书链接', '更新时间'],
            [1, [{'link': 'https://wit0jhu6kvu.feishu.cn/wiki/Current',
                  'text': '本周周报', 'type': 'mention'}],
             'IF(B2<>"",TEXT(NOW(),"yyyy-mm-dd hh:mm"),"")'],
        ]
        records = parse_registry_values(values)
        self.assertEqual(records[0]['sequence'], 1)
        self.assertEqual(records[0]['source_url'],
                         'https://wit0jhu6kvu.feishu.cn/wiki/Current')
        self.assertEqual(records[0]['_schema'], 'simple')


class TestRegistrySelection(unittest.TestCase):
    def test_simple_table_selects_highest_nonempty_sequence(self):
        records = parse_registry_values([
            ['序号', '飞书链接', '更新时间'],
            [1, [{'link': 'https://wit0jhu6kvu.feishu.cn/wiki/Old'}], 'formula'],
            [2, None, 'formula'],
            [3, [{'link': 'https://wit0jhu6kvu.feishu.cn/wiki/Current'}], 'formula'],
        ])
        selected = select_current_registry_row(records, NOW, HOSTS)
        self.assertEqual(selected.sequence, 3)
        self.assertEqual(selected.row_number, 4)

    def test_simple_table_duplicate_sequence_fails(self):
        records = parse_registry_values([
            ['序号', '飞书链接', '更新时间'],
            [1, [{'link': 'https://wit0jhu6kvu.feishu.cn/wiki/A'}], 'formula'],
            [1, [{'link': 'https://wit0jhu6kvu.feishu.cn/wiki/B'}], 'formula'],
        ])
        with self.assertRaisesRegex(RuntimeError, '序号重复'):
            select_current_registry_row(records, NOW, HOSTS)

    def test_selects_latest_eligible_and_reuses_without_new_row(self):
        records = [
            row('2026-W33', 'https://wit0jhu6kvu.feishu.cn/sheets/Old',
                '2026-08-13 09:00:00+08:00', number=2),
            row('2026-W34', 'https://wit0jhu6kvu.feishu.cn/sheets/Current',
                '2026-08-20 09:00:00+08:00', number=3),
            row('2026-W35', 'https://wit0jhu6kvu.feishu.cn/sheets/Future',
                '2026-08-27 09:00:00+08:00', number=4),
        ]
        selected = select_current_registry_row(records, NOW, HOSTS)
        self.assertEqual(selected.period_id, '2026-W34')
        self.assertEqual(selected.row_number, 3)

    def test_naive_effective_at_uses_beijing_time(self):
        records = [row(
            '2026-W34', 'https://wit0jhu6kvu.feishu.cn/sheets/Current',
            '2026-08-24 11:30:00')]
        selected = select_current_registry_row(records, NOW, HOSTS)
        self.assertEqual(selected.effective_at.isoformat(), '2026-08-24T03:30:00+00:00')

    def test_disabled_and_future_are_not_selected(self):
        records = [
            row('2026-W34', 'https://wit0jhu6kvu.feishu.cn/sheets/A',
                '2026-08-20 09:00:00+08:00', status='disabled'),
            row('2026-W35', 'https://wit0jhu6kvu.feishu.cn/sheets/B',
                '2026-08-27 09:00:00+08:00'),
        ]
        with self.assertRaisesRegex(RuntimeError, '不存在有效'):
            select_current_registry_row(records, NOW, HOSTS)

    def test_duplicate_period_fails(self):
        records = [
            row('2026-W34', 'https://wit0jhu6kvu.feishu.cn/sheets/A',
                '2026-08-20 09:00:00+08:00'),
            row('2026-W34', 'https://wit0jhu6kvu.feishu.cn/sheets/B',
                '2026-08-21 09:00:00+08:00', status='draft'),
        ]
        with self.assertRaisesRegex(RuntimeError, 'period_id 重复'):
            select_current_registry_row(records, NOW, HOSTS)

    def test_tied_latest_fails(self):
        records = [
            row('2026-W34A', 'https://wit0jhu6kvu.feishu.cn/sheets/A',
                '2026-08-20 09:00:00+08:00'),
            row('2026-W34B', 'https://wit0jhu6kvu.feishu.cn/sheets/B',
                '2026-08-20 09:00:00+08:00'),
        ]
        with self.assertRaisesRegex(RuntimeError, '并列'):
            select_current_registry_row(records, NOW, HOSTS)

    def test_latest_invalid_url_does_not_fallback(self):
        records = [
            row('2026-W33', 'https://wit0jhu6kvu.feishu.cn/sheets/Old',
                '2026-08-13 09:00:00+08:00'),
            row('2026-W34', 'https://evil.example/sheets/Bad',
                '2026-08-20 09:00:00+08:00'),
        ]
        with self.assertRaisesRegex(RuntimeError, '域名不允许'):
            select_current_registry_row(records, NOW, HOSTS)

    def test_monday_morning_carries_previous_period(self):
        records = [
            row('2026-W33', 'https://wit0jhu6kvu.feishu.cn/sheets/Old',
                '2026-08-13 09:00:00+08:00'),
            row('2026-W34', 'https://wit0jhu6kvu.feishu.cn/sheets/Current',
                '2026-08-20 09:00:00+08:00'),
        ]
        selected = select_for_scheduled_slot(records, NOW, HOSTS, 'monday_0730', '2026-W33')
        self.assertEqual(selected.period_id, '2026-W33')
        self.assertEqual(selected.selection_mode, 'monday_carryover')

    def test_monday_afternoon_requires_a_newer_period(self):
        records = [
            row('2026-W33', 'https://wit0jhu6kvu.feishu.cn/sheets/Old',
                '2026-08-13 09:00:00+08:00'),
            row('2026-W34', 'https://wit0jhu6kvu.feishu.cn/sheets/Current',
                '2026-08-20 09:00:00+08:00'),
        ]
        selected = select_for_scheduled_slot(records, NOW, HOSTS, 'monday_1530', '2026-W33')
        self.assertEqual(selected.period_id, '2026-W34')
        self.assertEqual(selected.selection_mode, 'monday_switch')
        with self.assertRaisesRegex(RuntimeError, '没有比上一周期'):
            select_for_scheduled_slot(records, NOW, HOSTS, 'monday_1530', '2026-W34')

    def test_weekday_does_not_silently_switch_to_pending_period(self):
        records = [
            row('2026-W33', 'https://wit0jhu6kvu.feishu.cn/sheets/Old',
                '2026-08-13 09:00:00+08:00'),
            row('2026-W34', 'https://wit0jhu6kvu.feishu.cn/sheets/Current',
                '2026-08-20 09:00:00+08:00'),
        ]
        selected = select_for_scheduled_slot(records, NOW, HOSTS, 'weekday_0730', '2026-W33')
        self.assertEqual(selected.period_id, '2026-W33')
        self.assertEqual(selected.selection_mode, 'weekday_steady')
        self.assertEqual(selected.pending_period_change['period_id'], '2026-W34')


if __name__ == '__main__':
    unittest.main()
