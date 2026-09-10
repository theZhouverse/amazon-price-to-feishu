import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock
from result_notification import completion_text, result_title, send_to_recipients, recipient_text


class ResultNotificationTest(unittest.TestCase):
    def test_requested_format(self):
        text = completion_text(period_id='seq-2', run_id='20260826_070010',
            started_at='07:00', finished_at='07:51', elapsed_seconds=3088.39,
            sheet_count=18, written_rows=622, blocked_count=103, error_ratio=.149,
            result_name='Result', result_url='https://example.com/sheet', local_data='D:/bundle.json')
        self.assertTrue(text.startswith('Hi，有个 Amazon 周报前端价格捕捉任务完成，请查收；\n\n周期：'))
        self.assertIn('本地数据：D:/bundle.json', text)
        self.assertIn('G531wP7WNiepV3krnrHcavqin6d', text)
        self.assertIn('阻断行：103', text)
        self.assertIn('完整耗时：51分28秒', text)
        self.assertNotIn('HTML局域网端口', text)
        self.assertNotIn('证据:', text)
        self.assertNotIn('需恢复', text)
        self.assertIn('周期：2026-W35（登记序号 seq-2）', text)

    def test_local_data_only_for_manager_and_rich_links(self):
        fc = Mock()
        fc.send_post_message.return_value = 'message'
        text = completion_text(period_id='seq-2', run_id='run',
            started_at='2026-08-26T07:00:03', finished_at='2026-08-26T07:51:32',
            elapsed_seconds=3088.39, sheet_count=18, written_rows=622,
            blocked_count=103, result_name='Result',
            result_url='https://example.com/sheet', local_data='D:/private.json')
        report = send_to_recipients(fc, ['ou_manager', 'ou_other'], text,
                                    local_data_open_id='ou_manager')
        self.assertEqual(len(report['sent']), 2)
        manager_post = fc.send_post_message.call_args_list[0].args[1]
        other_post = fc.send_post_message.call_args_list[1].args[1]
        self.assertIn('D:/private.json', json.dumps(manager_post))
        self.assertNotIn('private.json', json.dumps(other_post))
        self.assertNotIn('本地数据', json.dumps(other_post, ensure_ascii=False))
        self.assertNotIn('周期：', json.dumps(other_post, ensure_ascii=False))
        self.assertNotIn('运行编号：', json.dumps(other_post, ensure_ascii=False))
        self.assertNotIn('写入行：', json.dumps(other_post, ensure_ascii=False))
        links = [node for row in other_post['zh_cn']['content'] for node in row if node['tag'] == 'a']
        self.assertEqual(links[0], {'tag': 'a', 'text': 'Result', 'href': 'https://example.com/sheet'})
        self.assertEqual(links[1]['text'], '关于上述表格的简要说明')
        self.assertNotIn('模拟', text)
        fc.send_text_message.assert_not_called()

    def test_missing_manager_fails_closed_for_both_colons(self):
        for prefix in ['本地数据:', '本地数据：']:
            self.assertEqual(recipient_text('test\n' + prefix + ' D:/private', 'ou_other'), 'test')

    def test_dedup_and_failure_does_not_stop_others(self):
        fc = Mock()
        fc.send_text_message.side_effect = ['m1', RuntimeError('230013'), 'm3']
        report = send_to_recipients(fc, ['ou_1', 'ou_2', 'ou_1', '', 'ou_3'], 'test')
        self.assertEqual(len(report['sent']), 2)
        self.assertEqual(report['failed'][0]['open_id'], 'ou_2')
        self.assertEqual(fc.send_text_message.call_count, 3)

    def test_title_distinguishes_runs_on_same_day(self):
        first = result_title('seq-2', '20260826_070010')
        second = result_title('seq-2', '20260826_153000')
        self.assertNotEqual(first, second)
        self.assertTrue(first.endswith('20260826_070010'))
        self.assertIn('2026-W35', first)

    def test_expired_token_is_refreshed_before_final_notification(self):
        import httpx
        from feishu import FeishuClient
        fc = FeishuClient({'feishu_app_id': 'app', 'feishu_app_secret': 'secret'})
        fc.token = 'expired'
        fc._token_expires_at = 1
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={
                'code': 0, 'tenant_access_token': 'fresh', 'expire': 7200})
        fc._client.close()
        fc._client = httpx.Client(base_url='https://open.feishu.cn/open-apis', transport=httpx.MockTransport(handler))
        self.addCleanup(fc._client.close)
        from unittest import mock
        with mock.patch('feishu.time.monotonic', return_value=100):
            self.assertEqual(fc.auth(), 'fresh')
        self.assertEqual(len(calls), 1)

    def test_application_collaborators_api_and_delivery_error(self):
        import httpx
        from feishu import FeishuClient
        fc = FeishuClient({'feishu_app_id': 'test-app'})
        fc.token = 'test-token'
        def handler(request):
            if request.method == 'GET':
                self.assertIn('/application/v6/applications/test-app/collaborators', str(request.url))
                self.assertEqual(request.url.params['user_id_type'], 'open_id')
                return httpx.Response(200, json={'code': 0, 'data': {'collaborators': [
                    {'user_id': 'ou_a'}, {'user_id': 'ou_a'}, {'user_id': 'ou_b'}]}})
            return httpx.Response(400, json={'code': 230013, 'msg': 'Bot has NO availability to this user.'})
        fc._client.close()
        fc._client = httpx.Client(base_url='https://open.feishu.cn/open-apis', transport=httpx.MockTransport(handler))
        self.addCleanup(fc._client.close)
        self.assertEqual(fc.application_collaborators(), ['ou_a', 'ou_b'])
        with self.assertRaisesRegex(RuntimeError, '230013'):
            fc.send_text_message('ou_a', 'test')

    def test_notification_audit_persisted_and_manager_deduplicated(self):
        from main import _notify_run_collaborators
        fc = Mock()
        fc.application_collaborators.return_value = ['ou_a', 'ou_b']
        fc.send_text_message.return_value = 'message'
        with tempfile.TemporaryDirectory() as root:
            _notify_run_collaborators(fc, {'feishu_manager_open_id': 'ou_a'}, Mock(),
                                     'run', 'text', Path(root))
            data = json.loads((Path(root) / 'run_notifications.json').read_text(encoding='utf-8'))
        self.assertEqual(len(data['sent']), 2)
        self.assertEqual(fc.send_text_message.call_count, 2)
