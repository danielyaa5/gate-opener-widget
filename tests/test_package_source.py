import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
import hashlib

from scripts import package_source


class SourcePackageTests(unittest.TestCase):
    def test_archive_is_reproducible_and_manifest_covers_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / 'first.zip', Path(directory) / 'second.zip'
            payload = {'sample.txt': 'example', 'icon.png': b'\x89PNG\x00'}
            package_source.write_archive(payload, first)
            package_source.write_archive(payload, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with ZipFile(first) as archive:
                for line in archive.read('gate-opener/SHA256SUMS').decode().splitlines():
                    digest, name = line.split('  ', 1)
                    self.assertEqual(digest, hashlib.sha256(archive.read('gate-opener/' + name)).hexdigest())

    def test_export_omits_configuration_and_personal_signing(self):
        files = package_source.export_files()
        self.assertNotIn(package_source.EXCLUDED, files)
        self.assertNotIn('.env', files)
        self.assertFalse(any('.git/' in name or '.xcodeproj/' in name for name in files))
        self.assertIn('macos/project.yml', files)
        self.assertIn('ios/GateOpener/Shared/GateSettings.swift', files)
        self.assertNotIn('DEVELOPMENT_TEAM:', files['ios/project.yml'])
        for spec in package_source.PROJECT_SPECS:
            for value in package_source.personal_signing_values():
                self.assertNotIn(value, files[spec])
            for line in files[spec].splitlines():
                if 'bundleIdPrefix:' in line or 'GATE_BUNDLE_PREFIX:' in line:
                    self.assertTrue(line.strip().endswith('org.example'), line)
                if 'PRODUCT_BUNDLE_IDENTIFIER:' in line and '$(' not in line:
                    self.assertIn('org.example.GateOpener', line)
        self.assertIn('assets/macOS.xcassets/AppIcon.appiconset/icon_512x512@2x.png', files)
        self.assertIn('assets/iOS.xcassets/AppIcon.appiconset/icon.png', files)
        self.assertIn('.github/workflows/tests.yml', files)
        self.assertIn('SECURITY.md', files)
        self.assertIn('README.md', files)
        self.assertNotIn('RELEASE.md', files)
        self.assertIn('docs/index.html', files)
        self.assertTrue(files['docs/images/icon.png'].startswith(b'\x89PNG'))
        self.assertTrue(files['assets/iOS.xcassets/AppIcon.appiconset/icon.png'].startswith(b'\x89PNG'))

    def test_export_refuses_configured_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('GATE_API_SECRET=test-private-value\n')
            with patch.object(package_source, 'ROOT', root):
                with self.assertRaisesRegex(ValueError, 'export refused'):
                    package_source.check_local_values({'leak.txt': 'test-private-value'})
                package_source.check_local_values({'safe.txt': 'placeholder'})
                with self.assertRaises(ValueError):
                    package_source.check_local_values({'leak.png': b'test-private-value'})

    def test_export_refuses_swift_endpoint_and_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / package_source.EXCLUDED
            config.parent.mkdir(parents=True)
            config.write_text('static let apiSecret = "private-swift-value"\n'
                              'static let bridgeBaseURL = URL(string: "https://private.example")!')
            with patch.object(package_source, 'ROOT', root):
                for value in ['private-swift-value', 'https://private.example']:
                    with self.subTest(value=value), self.assertRaises(ValueError):
                        package_source.check_local_values({'leak.txt': value})

    def test_sanitize_replaces_any_team_and_bundle_prefix(self):
        spec = ('options:\n  bundleIdPrefix: com.someone\n'
                'settings:\n  base:\n    DEVELOPMENT_TEAM: ABCDE12345\n    GATE_BUNDLE_PREFIX: com.someone\n'
                'targets:\n  App:\n    settings:\n      base:\n        PRODUCT_BUNDLE_IDENTIFIER: com.someone.GateOpenerXYZ\n'
                '  Widget:\n    settings:\n      base:\n        PRODUCT_BUNDLE_IDENTIFIER: com.someone.GateOpenerXYZ.widget\n'
                '  Mac:\n    settings:\n      base:\n        PRODUCT_BUNDLE_IDENTIFIER: $(GATE_BUNDLE_PREFIX).GateOpenerMac\n')
        result = package_source.sanitize_project_spec(spec)
        for value in ['ABCDE12345', 'com.someone', 'DEVELOPMENT_TEAM']:
            self.assertNotIn(value, result)
        self.assertIn('PRODUCT_BUNDLE_IDENTIFIER: org.example.GateOpener\n', result)
        self.assertIn('PRODUCT_BUNDLE_IDENTIFIER: org.example.GateOpener.widget\n', result)
        self.assertIn('PRODUCT_BUNDLE_IDENTIFIER: $(GATE_BUNDLE_PREFIX).GateOpenerMac', result)

    def test_export_refuses_unsanitized_signing_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'ios').mkdir()
            (root / 'ios/project.yml').write_text('    DEVELOPMENT_TEAM: ABCDE12345\n  bundleIdPrefix: com.someone\n')
            with patch.object(package_source, 'ROOT', root):
                with self.assertRaises(ValueError):
                    package_source.check_local_values({'leak.txt': 'ABCDE12345'})
