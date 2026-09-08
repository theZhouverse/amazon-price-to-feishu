import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from diagnostics import save_evidence
from models import CrawlResult, PageStatus
from registry_freshness import assert_registry_fresh
from weekly_assets import WeeklyAssetStore
from weekly_result import write_weekly_result_columns
from test_weekly_result_write import FakeFeishu, manifest, result


class RegistryFreshnessTests(unittest.TestCase):
    def selection(self, period='seq-2', url='https://example/sheet'):
        return SimpleNamespace(period_id=period, source_url=url, row_number=3)

    def test_old_registry_identity_is_blocked_and_new_one_is_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            store = WeeklyAssetStore(Path(temp))
            now = datetime(2026, 8, 27, 9, tzinfo=timezone(timedelta(hours=8)))
            store.save('seq-2', {'created_at': (now - timedelta(days=9)).isoformat()})
            with self.assertRaisesRegex(RuntimeError, '超过 8 天'):
                assert_registry_fresh(store, self.selection(), now, 8)
            entry = assert_registry_fresh(store, self.selection('seq-3'), now, 8)
            self.assertEqual(entry['period_id'], 'seq-3')

    def test_same_sequence_cannot_change_url_to_reset_age(self):
        with tempfile.TemporaryDirectory() as temp:
            store = WeeklyAssetStore(Path(temp))
            now = datetime.now(timezone.utc)
            assert_registry_fresh(store, self.selection(), now, 8)
            with self.assertRaisesRegex(RuntimeError, '链接发生变化'):
                assert_registry_fresh(store, self.selection(url='https://example/other'), now, 8)


class LightweightEvidenceTests(unittest.TestCase):
    def test_error_evidence_never_reads_or_writes_html(self):
        with tempfile.TemporaryDirectory() as temp, patch('diagnostics.DEBUG_DIR', Path(temp)):
            tab = Mock()
            tab.html = '<html>large</html>'
            cr = CrawlResult(asin='B000000001', status=PageStatus.CRAWL_ERROR, error='failed')
            path = save_evidence('run', 'CPD03', cr, tab, {})
            self.assertTrue((path / 'diagnostic.json').is_file())
            self.assertFalse((path / 'page.html').exists())


class FullCapacityMaintenanceTests(unittest.TestCase):
    def test_legacy_writer_finds_asin_after_row_2000(self):
        asins = [f'B0{i:08d}' for i in range(2001)]
        fc = FakeFeishu(asins=asins)
        target = asins[-1]
        report = write_weekly_result_columns(
            fc, manifest(), 'run1', {'PD03': [result(target, archive=False)]},
            {'html_archive_required': False})
        self.assertEqual(report['written_rows'], 1)
        self.assertTrue(any(rng == 'H2003:O2003' for _, _, rng, _ in fc.writes))


class SchedulerPolicyTests(unittest.TestCase):
    def test_overlap_is_successfully_coalesced_and_logs_are_unique(self):
        script = (Path(__file__).resolve().parents[1] / 'bin' / 'scheduled_run.ps1').read_text(encoding='utf-8')
        self.assertIn('$exitCode -eq 75', script)
        self.assertIn('$exitCode = 0', script)
        self.assertIn('$PID.log', script)
        self.assertIn('$runnerError = $null', script)
        self.assertIn('finally {', script)
        self.assertIn('END exit=$exitCode', script)

    def test_scheduler_uses_gui_launcher_before_powershell(self):
        root = Path(__file__).resolve().parents[1]
        installer = (root / 'bin' / 'schedule.ps1').read_text(encoding='utf-8')
        launcher = root / 'bin' / 'hidden_ps1.vbs'
        scheduled_bat = (root / 'bin' / 'scheduled_run.bat').read_text(encoding='utf-8')
        self.assertTrue(launcher.is_file())
        self.assertIn("New-ScheduledTaskAction -Execute 'wscript.exe'", installer)
        self.assertIn('hidden_ps1.vbs', installer)
        self.assertIn('wscript.exe //B //NoLogo', scheduled_bat)
        self.assertIn('WindowStyle Hidden', launcher.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
