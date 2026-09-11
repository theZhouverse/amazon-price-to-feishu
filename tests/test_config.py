# -*- coding: utf-8 -*-
"""配置来源职责、优先级与防冲突测试。"""
import json
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    'structured_config_under_test', PROJECT_ROOT / 'app' / 'config.py'
)
config_mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(config_mod)


class TestConfigSources(unittest.TestCase):
    def _write_config(self, root: Path, data: dict | None = None) -> Path:
        path = root / 'config.json'
        path.write_text(json.dumps(data or {}, ensure_ascii=False), encoding='utf-8')
        return path

    def test_secret_is_rejected_in_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = self._write_config(root, {'feishu_app_secret': 'must-not-be-here'})
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, '禁止配置'):
                    config_mod.load_config(path)

    def test_root_dotenv_supplies_secret(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = self._write_config(root)
            (root / '.env').write_text('FS_APP_SECRET=from_file\n', encoding='utf-8')
            with patch.object(config_mod, 'PROJECT_ROOT', root), \
                    patch.dict(os.environ, {}, clear=True):
                cfg = config_mod.load_config(path)
            self.assertEqual(cfg['feishu_app_secret'], 'from_file')

    def test_system_environment_overrides_dotenv(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = self._write_config(root)
            (root / '.env').write_text('FS_APP_SECRET=from_file\n', encoding='utf-8')
            with patch.object(config_mod, 'PROJECT_ROOT', root), \
                    patch.dict(os.environ, {'FS_APP_SECRET': 'from_system'}, clear=True):
                cfg = config_mod.load_config(path)
            self.assertEqual(cfg['feishu_app_secret'], 'from_system')

    def test_legacy_credential_directory_is_supported_without_duplicate_app_id(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = self._write_config(root, {'feishu_app_id': 'cli_test'})
            env_dir = root / '.env'
            env_dir.mkdir()
            (env_dir / '飞书凭证.txt').write_text(
                'cli_test\nfrom_legacy_file\n', encoding='utf-8')
            with patch.object(config_mod, 'PROJECT_ROOT', root), \
                    patch.dict(os.environ, {}, clear=True):
                cfg = config_mod.load_config(path)
            self.assertEqual(cfg['feishu_app_secret'], 'from_legacy_file')

    def test_legacy_credential_app_id_mismatch_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = self._write_config(root, {'feishu_app_id': 'cli_expected'})
            env_dir = root / '.env'
            env_dir.mkdir()
            (env_dir / '飞书凭证.txt').write_text(
                'cli_other\nsecret\n', encoding='utf-8')
            with patch.object(config_mod, 'PROJECT_ROOT', root), \
                    patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'App ID'):
                    config_mod.load_config(path)

    def test_explicit_runtime_overrides_are_supported_for_portable_deployments(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = self._write_config(root)
            env = {
                'AMAZON_HTML_ARCHIVE_ROOT': '/data/htmls',
                'AMAZON_HTML_ARCHIVE_ENABLED': 'true',
                'AMAZON_HTML_SERVER_PORT': '9876',
                'AMAZON_WORKERS': '2',
                'AMAZON_FEEDBACK_ENABLED': 'false',
                'AMAZON_CA_LOCATION_MODE': 'direct_no_postal',
            }
            with patch.object(config_mod, 'PROJECT_ROOT', root), \
                    patch.dict(os.environ, env, clear=True):
                cfg = config_mod.load_config(path)
            self.assertEqual(cfg['html_archive_root'], '/data/htmls')
            self.assertTrue(cfg['html_archive_enabled'])
            self.assertEqual(cfg['html_server_port'], 9876)
            self.assertEqual(cfg['workers'], 2)
            self.assertFalse(cfg['feedback']['enabled'])
            self.assertEqual(cfg['ca_location_mode'], 'direct_no_postal')

    def test_invalid_runtime_boolean_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = self._write_config(root)
            with patch.object(config_mod, 'PROJECT_ROOT', root), \
                    patch.dict(os.environ, {'AMAZON_HTML_ARCHIVE_ENABLED': 'maybe'}, clear=True):
                with self.assertRaisesRegex(RuntimeError, '必须是布尔值'):
                    config_mod.load_config(path)

    def test_invalid_feedback_runtime_boolean_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = self._write_config(root)
            with patch.object(config_mod, 'PROJECT_ROOT', root), \
                    patch.dict(os.environ, {'AMAZON_FEEDBACK_ENABLED': 'maybe'}, clear=True):
                with self.assertRaisesRegex(RuntimeError, '必须是布尔值'):
                    config_mod.load_config(path)

    def test_invalid_runtime_port_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = self._write_config(root)
            with patch.object(config_mod, 'PROJECT_ROOT', root), \
                    patch.dict(os.environ, {'AMAZON_HTML_SERVER_PORT': '70000'}, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'html_server_port'):
                    config_mod.load_config(path)


if __name__ == '__main__':
    unittest.main()
