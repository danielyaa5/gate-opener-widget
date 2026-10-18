import base64
import json
import argparse
import contextlib
import io
import plistlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import ensure_api_secret, install_launch_agent, setup_code


class SetupTests(unittest.TestCase):
    def test_generating_secret_preserves_custom_bridge_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / '.env'
            env.write_text('GATE_API_SECRET=\nGATE_BRIDGE_PORT=9000\nGATE_BRIDGE_HOST=127.0.0.1\nGATE_PULSE_SECONDS=0.2\n')
            with patch.object(ensure_api_secret, 'ENV_PATH', env), contextlib.redirect_stdout(io.StringIO()):
                ensure_api_secret.main()
                first = env.read_bytes()
                ensure_api_secret.main()
            self.assertEqual(first, env.read_bytes())
            self.assertIn('GATE_BRIDGE_PORT=9000', env.read_text())
            self.assertIn('GATE_BRIDGE_HOST=127.0.0.1', env.read_text())
            self.assertIn('GATE_PULSE_SECONDS=0.2', env.read_text())
            self.assertEqual(env.stat().st_mode & 0o777, 0o600)

    def test_setup_code_round_trips_url_and_secret(self):
        code = setup_code.encode('https://bridge.example', 'synthetic-secret-' + 'x' * 32)
        self.assertTrue(code.startswith('gate1:'))
        self.assertNotIn('=', code)
        payload = code.removeprefix('gate1:')
        decoded = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        self.assertEqual(decoded, {'url': 'https://bridge.example', 'secret': 'synthetic-secret-' + 'x' * 32})

    def test_setup_code_uses_explicit_url(self):
        config = SimpleNamespace(api_secret='synthetic-secret-' + 'x' * 32, port=8765)
        output = io.StringIO()
        with patch.object(setup_code.Config, 'from_env', return_value=config), \
             patch('sys.argv', ['setup_code.py', '--url', 'https://bridge.example/']), \
             contextlib.redirect_stdout(output):
            setup_code.main()
        self.assertIn(setup_code.encode('https://bridge.example', config.api_secret), output.getvalue())

    def test_configuration_rejects_non_origin_urls(self):
        for url in ['file:///etc/passwd', 'https://user:password@example.com', 'https://example.com/api', 'https://example.com?secret=x']:
            with self.subTest(url=url), self.assertRaises(argparse.ArgumentTypeError):
                setup_code.validate_url(url)

    def test_service_installer_escapes_paths_without_starting_service(self):
        with tempfile.TemporaryDirectory(prefix='gate & test ') as directory:
            root = Path(directory)
            (root / '.venv/bin').mkdir(parents=True)
            (root / '.venv/bin/python').touch()
            (root / '.env').touch()
            output = root / 'LaunchAgents/test.plist'
            with patch.object(install_launch_agent, 'PROJECT_ROOT', root), \
                 patch.object(install_launch_agent, 'DESTINATION', output), \
                 patch.object(install_launch_agent, 'LOG_DIR', root / 'Logs & Output'), \
                 patch.object(install_launch_agent, 'run', return_value=SimpleNamespace(returncode=1)), \
                 contextlib.redirect_stdout(io.StringIO()):
                install_launch_agent.main()
            payload = plistlib.loads(output.read_bytes())
            self.assertEqual(payload['WorkingDirectory'], str(root))
            self.assertEqual(payload['StandardOutPath'], str(root / 'Logs & Output/gate-bridge.log'))
