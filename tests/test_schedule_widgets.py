"""Regresi widget Schedule berbeda antar-akun; tanpa jaringan atau upload."""
from datetime import datetime,timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import schedule_ui as ui
from schedule_store import WIB


class ScheduleWidgetTests(unittest.TestCase):
    def setUp(self):
        self.target=datetime(2026,11,13,10,10,tzinfo=WIB)
        self.snapshot={'variant':'legacy','selected':True,'date':'2026-11-13','time':'10:10','timezone':'(GMT+7) Local Time','button':'Schedule'}

    def test_both_display_formats_require_matching_date_time_zone_and_button(self):
        for date in ['2026-11-13','Nov 13, 2026']:
            self.assertTrue(ui.matches_target({**self.snapshot,'date':date},self.target))
        for key,value in [('selected',False),('date','2026-11-14'),('time','10:15'),('timezone',None),('timezone','(GMT+8) Local Time'),('button','Post')]:
            with self.subTest(key=key,value=value):
                self.assertFalse(ui.matches_target({**self.snapshot,key:value},self.target))

    def test_legacy_date_day_is_selected_by_calendar_position_not_duplicate_number(self):
        snapshot={**self.snapshot,'date':'2026-11-12'}
        days=[{'day':d,'valid':True} for d in [26,27,28,29,30,31]+list(range(1,31))+list(range(1,7))]
        calls=[]
        ui.configure_legacy(self.target,snapshot,lambda e:{'month':11,'year':2026,'days':days},lambda expr,label:calls.append((expr,label)),lambda s:self.fail('No time change needed'),lambda *a:self.snapshot)
        self.assertEqual(len(calls),2)
        self.assertIn('.slice(18,19)',calls[1][0])

    def test_disabled_legacy_day_never_clicks_date(self):
        snapshot={**self.snapshot,'date':'2026-11-12'}
        days=[{'day':d,'valid':d!=13} for d in range(1,31)]
        clicks=[]
        with self.assertRaises(ValueError):
            ui.configure_legacy(self.target,snapshot,lambda e:{'month':11,'year':2026,'days':days},lambda e,l:clicks.append(l),lambda s:None,lambda *a:None)
        self.assertEqual(clicks,['tanggal Schedule readonly'])

    def test_legacy_time_uses_hour_minute_columns_and_waits_for_animation(self):
        calls=[];waits=[]
        ui.configure_legacy(self.target,{**self.snapshot,'time':'08:50'},lambda e:None,lambda e,l:calls.append((e,l)),lambda s:calls.append((s,'close')),lambda predicate,message:waits.append(predicate(self.snapshot)))
        self.assertEqual(len(calls),4)
        self.assertIn('tiktok-timepicker-left',calls[1][0])
        self.assertIn('tiktok-timepicker-right',calls[2][0])
        self.assertEqual(waits,[True,True])
        self.assertIn('scheduled-title-container',calls[-1][0])

    def test_ambiguous_controls_timeout_without_clicking_or_submitting(self):
        clicks=[]
        def read(ws,expression,mid):
            if expression==ui.SNAPSHOT_JS:return None
            if 'Local Time' in expression:return 'Asia/Jakarta'
            if 'postSchedule' in expression:return True
            return False
        with patch.object(ui,'now_wib',return_value=self.target-timedelta(hours=2)),patch.object(ui.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'ambigu'):
                ui.configure(None,1,self.target,read,lambda *a:clicks.append(a))
        self.assertEqual(clicks,[])

    def test_wrong_legacy_timezone_never_opens_picker(self):
        with self.assertRaisesRegex(ValueError,'WIB'):
            ui.configure_legacy(self.target,{**self.snapshot,'timezone':'wrong'},lambda *a:self.fail(),lambda *a:self.fail(),lambda *a:self.fail(),lambda *a:self.fail())
