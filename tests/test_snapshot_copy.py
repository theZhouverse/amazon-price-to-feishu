# -*- coding: utf-8 -*-
import unittest
from unittest.mock import Mock

import httpx

from feishu import FeishuClient


class TestSnapshotCopy(unittest.TestCase):
    def make_client(self):
        client = object.__new__(FeishuClient)
        client.cfg = {'feishu_app_id': 'x', 'feishu_app_secret': 'y'}
        client.token = 'token'
        client._client = Mock()
        return client

    def test_copy_file_uses_sheet_and_root_folder(self):
        client = self.make_client()
        response = Mock()
        response.json.return_value = {
            'code': 0,
            'data': {'file': {'token': 'copy-token', 'name': 'TEST', 'type': 'sheet'}},
        }
        client._client.post.return_value = response
        got = client.copy_file('source-token', 'sheet', 'TEST', '')
        self.assertEqual(got['token'], 'copy-token')
        client._client.post.assert_called_once_with(
            '/drive/v1/files/source-token/copy',
            json={'name': 'TEST', 'type': 'sheet', 'folder_token': ''},
            headers={'Authorization': 'Bearer token'})

    def test_copy_file_requires_returned_token(self):
        client = self.make_client()
        response = Mock()
        response.json.return_value = {'code': 0, 'data': {'file': {}}}
        client._client.post.return_value = response
        with self.assertRaisesRegex(RuntimeError, '缺少副本 Token'):
            client.copy_file('source-token', 'sheet', 'TEST')

    def test_copy_file_recovers_copy_created_before_504(self):
        client = self.make_client()
        timeout_response = Mock(status_code=504)
        timeout_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            'gateway timeout', request=Mock(), response=timeout_response)
        listing = Mock(status_code=200)
        listing.json.return_value = {
            'code': 0,
            'data': {'files': [{'name': 'TEST', 'type': 'sheet', 'token': 'recovered-token'}]},
        }
        client._client.post.return_value = timeout_response
        client._client.get.return_value = listing
        got = client.copy_file('source-token', 'sheet', 'TEST')
        self.assertEqual(got['token'], 'recovered-token')
        client._client.post.assert_called_once()
        client._client.get.assert_called_once()

    def test_copy_file_retries_transport_timeout_then_succeeds(self):
        client = self.make_client()
        success = Mock(status_code=200)
        success.json.return_value = {
            'code': 0,
            'data': {'file': {'token': 'copy-token', 'name': 'TEST', 'type': 'sheet'}},
        }
        client._client.post.side_effect = [httpx.ReadTimeout('timeout'), success]
        with unittest.mock.patch('feishu.time.sleep'):
            got = client.copy_file('source-token', 'sheet', 'TEST')
        self.assertEqual(got['token'], 'copy-token')
        self.assertEqual(client._client.post.call_count, 2)

    def test_structure_hash_is_stable(self):
        client = self.make_client()
        client.query_sheets = Mock(return_value=[{
            'sheet_id': 's1', 'title': 'PD03', 'index': 0,
            'grid_properties': {'row_count': 100, 'column_count': 20},
        }])
        client.read_values = Mock(return_value=[['ASIN', 'SKU']])
        first = client.spreadsheet_structure('token')
        second = client.spreadsheet_structure('token')
        self.assertEqual(first, second)
        self.assertEqual(first['sheet_count'], 1)
        self.assertEqual(first['sheets'][0]['sample'], [['ASIN', 'SKU']])

    def test_query_sheets_falls_back_to_v2_on_server_error(self):
        client = self.make_client()
        failed = Mock(status_code=500)
        success = Mock(status_code=200)
        success.json.return_value = {'code': 0, 'data': {'sheets': [{
            'sheetId': 's1', 'title': 'PD03', 'rowCount': 100, 'columnCount': 20,
        }]}}
        client._client.get.side_effect = [failed, success]
        got = client.query_sheets('token')
        self.assertEqual(got[0]['sheet_id'], 's1')
        self.assertEqual(got[0]['grid_properties']['row_count'], 100)
        self.assertEqual(client._client.get.call_count, 2)

    def test_read_values_batch_chunks_ranges(self):
        client = self.make_client()
        response = Mock()
        response.json.return_value = {'code': 0, 'data': {'valueRanges': []}}
        client._client.get.return_value = response
        client.read_values_batch('token', [f's{i}!A1:P10' for i in range(11)])
        self.assertEqual(client._client.get.call_count, 2)

    def test_wait_structure_retries_until_ready(self):
        client = self.make_client()
        client.spreadsheet_structure = Mock(side_effect=[
            RuntimeError('server error'), {'sheet_count': 1, 'sheets': [], 'sha256': 'x'},
        ])
        with unittest.mock.patch('feishu.time.sleep'):
            got = client.wait_spreadsheet_structure('copy')
        self.assertEqual(got['sha256'], 'x')
        self.assertEqual(client.spreadsheet_structure.call_count, 2)

    def test_create_spreadsheet_requires_token(self):
        client = self.make_client()
        response = Mock()
        response.json.return_value = {'code': 0, 'data': {'spreadsheet': {}}}
        client._client.post.return_value = response
        with self.assertRaisesRegex(RuntimeError, '缺少 spreadsheet_token'):
            client.create_spreadsheet('TEST')

    def test_add_sheet_returns_id(self):
        client = self.make_client()
        response = Mock()
        response.json.return_value = {'code': 0, 'data': {'replies': [{
            'addSheet': {'properties': {'sheetId': 'result-id'}},
        }]}}
        client._client.post.return_value = response
        self.assertEqual(client.add_sheet('book', 'TEST_RESULT'), 'result-id')


if __name__ == '__main__':
    unittest.main()
