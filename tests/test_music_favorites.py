"""Rotasi SQLite dan editor musik disimulasikan; tanpa koneksi TikTok."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import music_favorites as music

TRACKS=[{'title':title,'detail':'00:30 · Artist '+title} for title in ['A','B','C']]


class MusicSimulator:
    def __init__(self,pages=None,save_loss=False):
        self.pages=pages if pages is not None else [TRACKS]
        self.page=0;self.timeline=[];self.saved=[];self.editor=False
        self.target=None;self.events=[];self.save_loss=save_loss

    def point(self,target):
        self.target=target
        return {'x':1,'y':2}

    def read(self,ws,expression,mid):
        if expression==music.SOUNDS_JS:return self.point('open')
        if expression==music.FAVORITES_JS:return self.point('favorites')
        if expression==music.SAVE_EDITOR_JS:return self.point('save')
        if expression==music.CANCEL_EDITOR_JS:return self.point('cancel')
        if expression==music.FAVORITES_RESET_JS:self.page=0;return True
        if expression in (music.FAVORITES_READ_JS,music.FAVORITES_NEXT_JS):
            state={'items':self.pages[self.page],'bottom':self.page==len(self.pages)-1,'busy':False,'top':self.page*100}
            if expression==music.FAVORITES_NEXT_JS:self.page=min(len(self.pages)-1,self.page+1)
            return state
        if expression==music.AUDIO_TITLES_JS:return list(self.timeline)
        if expression==music.UPLOAD_READY_JS:return not self.editor
        for item in self.pages[self.page]:
            if expression==music.add_music_js(item):return self.point(item)
        if 'svg[data-icon="plus-bold"]' in expression:return None
        raise AssertionError('Unexpected music UI expression')

    def click(self,ws,x,y,mid):
        target=self.target;self.events.append(target)
        if target=='open':self.editor=True;self.timeline=list(self.saved)
        elif target=='save':self.saved=[] if self.save_loss else list(self.timeline);self.editor=False
        elif target=='cancel':self.editor=False
        elif isinstance(target,dict):self.timeline.append(target['title'])
        return mid+2


class MusicFavoritesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)

    def apply(self,sim,content_id=1):
        with patch.object(music.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
            return music.apply_favorite_music(None,1,self.base,'akun1',content_id,sim.read,sim.click)

    def test_rotation_repeats_and_retry_does_not_advance(self):
        results=[music.reserve_music(self.base,'akun1',cid,TRACKS)['title'] for cid in [1,2,1,3,4]]
        self.assertEqual(results,['A','B','A','C','A'])
        self.assertEqual(music.reserve_music(self.base,'akun2',1,TRACKS)['title'],'A')
        self.assertEqual(music.reserve_music(self.base,'akun1',2,list(reversed(TRACKS)))['title'],'B')

    def test_missing_previous_song_does_not_replace_pair_or_advance(self):
        music.reserve_music(self.base,'akun1',1,TRACKS)
        with self.assertRaises(music.MusicError):music.reserve_music(self.base,'akun1',1,TRACKS[1:])
        self.assertEqual(music.reserve_music(self.base,'akun1',2,TRACKS)['title'],'B')

    def test_concurrent_reservation_initializes_once(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(lambda _:music.reserve_music(self.base,'akun1',1,TRACKS),range(12)))
        self.assertEqual([r['title'] for r in results],['A']*12)
        self.assertEqual(music.reserve_music(self.base,'akun1',2,TRACKS)['title'],'B')

    def test_unknown_existing_database_is_preserved(self):
        path=self.base/'db/music_rotation.db';path.parent.mkdir()
        with sqlite3.connect(path) as conn:conn.execute('CREATE TABLE old_data(value TEXT)')
        before=path.read_bytes()
        with self.assertRaises(music.MusicError):music.reserve_music(self.base,'akun1',1,TRACKS)
        self.assertEqual(path.read_bytes(),before)

    def test_apply_scrolls_favorites_and_verifies_saved_timeline(self):
        music.reserve_music(self.base,'akun1',1,TRACKS)
        music.reserve_music(self.base,'akun1',2,TRACKS)
        sim=MusicSimulator(pages=[TRACKS[:2],TRACKS[2:]])
        self.apply(sim,content_id=3)
        self.assertEqual(sim.saved,['C'])
        self.assertEqual(sim.events,['open','favorites',TRACKS[2],'save','open','cancel'])
        self.assertFalse(sim.editor)

    def test_retry_does_not_duplicate_existing_music(self):
        sim=MusicSimulator();sim.saved=['A']
        self.apply(sim)
        self.assertFalse(any(isinstance(event,dict) for event in sim.events))
        self.assertEqual(sim.saved,['A'])

    def test_empty_favorites_stops_before_save(self):
        sim=MusicSimulator(pages=[[]])
        with self.assertRaisesRegex(music.MusicError,'Favorites kosong'):self.apply(sim)
        self.assertNotIn('save',sim.events)
        self.assertFalse((self.base/'db/music_rotation.db').exists())

    def test_save_losing_music_fails_verification(self):
        sim=MusicSimulator(save_loss=True)
        with self.assertRaisesRegex(music.MusicError,'tidak bertahan'):self.apply(sim)
        self.assertEqual(sim.events.count('save'),1)

    def test_ambiguous_list_or_different_existing_audio_stops(self):
        with self.assertRaises(music.MusicError):self.apply(MusicSimulator(pages=[[TRACKS[0],TRACKS[0]]]))
        sim=MusicSimulator();sim.saved=['Unknown']
        with self.assertRaises(music.MusicError):self.apply(sim)
        self.assertNotIn('save',sim.events)


if __name__=='__main__':unittest.main()
