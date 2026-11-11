"""Pemilihan profil internal memakai fixture; tidak membuka Chrome/TikTok."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import chrome_profile_path as profiles
import bot_settings
import daily_orchestrator as bot


class ChromeProfileTests(unittest.TestCase):
    def test_directory_resolves_slug_and_folder_and_refuses_missing_pin(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);(base/'db').mkdir()
            target=base/'chrome-profiles/baihaqi.ishak5'
            (target/'Profile 19').mkdir(parents=True)
            with sqlite3.connect(base/'db/tiktok_bot.db') as conn:
                conn.execute('CREATE TABLE accounts(slug,chrome_profile_path,cdp_port)')
                conn.execute('INSERT INTO accounts VALUES(?,?,?)',('akun6',str(target),9226))
            document={'version':1,'values':{},'accounts':{'akun6':{'chrome_profile_directory':'Profile 19'}},'schedules':{}}
            bot_settings.save_document(base,document)
            with patch.object(profiles,'BASE',base):
                for name in ('akun6','baihaqi.ishak5'):
                    self.assertEqual(profiles.directory(name,9226),'Profile 19')
                with self.assertRaises(ValueError):profiles.directory('akun6',9225)
                (target/'Profile 19').rmdir()
                with self.assertRaisesRegex(ValueError,'tidak ditemukan'):profiles.directory('akun6',9226)

    def test_invalid_directory_cannot_escape_profile_root(self):
        for name in ('../Default','Profile 0','Profile 19/../Default','', 'Default\n'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                bot_settings.validate_values({'chrome_profile_directory':name},partial=True)

    def test_guard_waits_for_version_and_accepts_exact_profile(self):
        with patch.object(bot,'eval_js',side_effect=[None,'/profiles/baihaqi/Profile 19']),patch.object(bot.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bot.verify_browser_profile(object(),Path('/profiles/baihaqi/Profile 19'),10),12)

    def test_wrong_profile_never_navigates_or_uploads(self):
        with patch.object(bot,'eval_js',return_value='/profiles/baihaqi/Profile 3'),patch.object(bot,'send_cdp') as send:
            with self.assertRaises(bot.StepFailed):
                bot.verify_browser_profile(object(),Path('/profiles/baihaqi/Profile 19'),1)
            send.assert_not_called()

    def test_unreadable_profile_fails_closed(self):
        with patch.object(bot,'eval_js',return_value=None),patch.object(bot.time,'sleep'):
            with self.assertRaises(bot.StepFailed):bot.verify_browser_profile(object(),Path('/profiles/Default'),1)

    def test_missing_pin_stops_before_creating_diagnostic_tab(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(bot.requests,'put') as create:
            success,error=bot.post_one_video(9226,'unused.mp4','caption','123','auto',expected_profile=Path(temp)/'Profile 19')
        self.assertFalse(success)
        self.assertIn('tidak ditemukan',error)
        create.assert_not_called()

    def test_mismatch_closes_only_owned_diagnostic_tab_without_upload(self):
        with tempfile.TemporaryDirectory() as temp:
            expected=Path(temp)/'Profile 19';expected.mkdir()
            response=MagicMock();response.json.return_value={'id':'owned-diagnostic','webSocketDebuggerUrl':'ws://127.0.0.1:9226/devtools/page/owned-diagnostic'}
            with patch.object(bot.requests,'put',return_value=response),patch.object(bot.requests,'get') as close,patch.object(bot.websocket,'create_connection',return_value=MagicMock()),patch.object(bot,'eval_js',return_value=str(Path(temp)/'Profile 6')),patch.object(bot,'send_cdp') as send,patch.object(bot,'capture_upload_diagnostic') as capture:
                success,error=bot.post_one_video(9226,'unused.mp4','caption','123','auto',expected_profile=expected)
            self.assertFalse(success)
            self.assertIn('tidak sesuai',error)
            close.assert_called_once_with('http://127.0.0.1:9226/json/close/owned-diagnostic',timeout=3)
            send.assert_not_called();capture.assert_not_called()
