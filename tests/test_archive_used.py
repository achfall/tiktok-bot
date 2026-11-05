"""Uji filesystem + SQLite tanpa browser, API caption, atau upload."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from archive_used import archive_content


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.product = self.root / 'Jingsu'
        self.product.mkdir()
        self.source = self.product / 'video.mp4'
        self.source.write_bytes(b'original video')
        self.db = self.root / 'test.db'
        self.conn = sqlite3.connect(self.db)
        self.addCleanup(self.conn.close)
        self.conn.executescript((ROOT / 'scripts/db/schema.sql').read_text())
        self.conn.execute("INSERT INTO accounts (slug,chrome_profile_path,cdp_port) VALUES ('akun1','profile',9221)")
        self.conn.execute("INSERT INTO products (account_id,folder_path,folder_name,sort_order) VALUES (1,?,'Jingsu',1)", (str(self.product),))
        self.conn.execute("INSERT INTO contents (product_id,file_path,status) VALUES (1,?,'used')", (str(self.source),))
        self.conn.execute("INSERT INTO posts_log (account_id,product_id,content_id,status) VALUES (1,1,1,'draft')")
        self.conn.commit()

    def test_archive_scan_and_reuse_original_filename(self):
        archived = archive_content(self.conn, 1)
        self.assertFalse(self.source.exists())
        self.assertEqual(archived.read_bytes(), b'original video')
        self.assertEqual(self.conn.execute('SELECT file_path,status FROM contents').fetchone(), (str(archived), 'used'))
        spec = importlib.util.spec_from_file_location('scan', ROOT / 'scripts/db/scan_contents.py')
        scan = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scan)
        with patch.object(scan, 'DB_PATH', self.db), patch.object(sys, 'argv', ['scan', 'akun1']), contextlib.redirect_stdout(io.StringIO()):
            scan.main()
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM contents').fetchone()[0], 1)
            self.source.write_bytes(b'new video with same filename')
            scan.main()
        self.assertEqual(self.conn.execute("SELECT file_path FROM contents WHERE status='unused'").fetchall(), [(str(self.source),)])
        self.assertEqual(archived.read_bytes(), b'original video')

    def test_collision_never_overwrites(self):
        archive = self.product / 'sudah-dipakai'
        archive.mkdir()
        existing = archive / self.source.name
        existing.write_bytes(b'older archive')
        target = archive_content(self.conn, 1)
        self.assertNotEqual(target, existing)
        self.assertEqual(existing.read_bytes(), b'older archive')
        self.assertEqual(target.read_bytes(), b'original video')

    def test_failed_or_uncertain_posts_do_not_move(self):
        for status in ('failed', 'uncertain'):
            self.conn.execute('UPDATE posts_log SET status=?', (status,))
            self.conn.commit()
            with self.assertRaises(ValueError):
                archive_content(self.conn, 1)
            self.assertTrue(self.source.is_file())

    def test_database_failure_restores_source_and_used_status(self):
        self.conn.execute("CREATE TRIGGER reject_archive BEFORE UPDATE OF file_path ON contents BEGIN SELECT RAISE(ABORT,'test disk/database failure'); END")
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            archive_content(self.conn, 1)
        self.assertEqual(self.source.read_bytes(), b'original video')
        self.assertEqual(list((self.product / 'sudah-dipakai').iterdir()), [])
        self.assertEqual(self.conn.execute('SELECT file_path,status FROM contents').fetchone(), (str(self.source), 'used'))

    def test_repeat_and_manual_delete_do_not_recreate_file(self):
        target = archive_content(self.conn, 1)
        self.assertEqual(archive_content(self.conn, 1), target)
        target.unlink()  # Operator menghapus video sendiri.
        self.assertEqual(archive_content(self.conn, 1), target)
        self.assertFalse(target.exists())
        self.assertEqual(self.conn.execute('SELECT status FROM contents').fetchone()[0], 'used')


if __name__ == '__main__':
    unittest.main()
