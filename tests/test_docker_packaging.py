# -*- coding: utf-8 -*-
"""Static checks for the portable Docker runtime contract."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DockerPackagingTests(unittest.TestCase):
    def test_dockerfile_does_not_copy_ignored_tests_or_credentials(self):
        text = (ROOT / 'deploy' / 'docker' / 'Dockerfile').read_text(encoding='utf-8')
        self.assertNotIn('COPY tests', text)
        self.assertNotIn('COPY .env', text)
        self.assertIn('chromium', text)
        self.assertIn('tini', text)

    def test_entrypoint_uses_system_cron_format(self):
        text = (ROOT / 'deploy' / 'docker' / 'entrypoint.sh').read_text(encoding='utf-8')
        self.assertIn('/etc/cron.d/amazon-daily', text)
        self.assertNotIn('crontab /etc/cron.d/amazon-daily', text)
        self.assertIn('/app/outputs/logs', text)

    def test_compose_persists_outputs_and_html_and_has_healthcheck(self):
        text = (ROOT / 'deploy' / 'docker' / 'docker-compose.yml').read_text(encoding='utf-8')
        self.assertIn('./volumes/outputs:/app/outputs', text)
        self.assertIn('./volumes/htmls:/app/htmls', text)
        self.assertIn('healthcheck:', text)
        self.assertIn('AMAZON_CONFIG_FILE', text)


if __name__ == '__main__':
    unittest.main()
