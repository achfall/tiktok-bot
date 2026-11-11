"""Tour dikenali lewat struktur spesifik; tidak ada klik pada Chrome/TikTok nyata."""
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from upload_overlays import dismiss_known_tours


class UploadOverlayTests(unittest.TestCase):
    def test_no_tour_does_not_click(self):
        evaluate=MagicMock(return_value=None);click=MagicMock()
        self.assertEqual(dismiss_known_tours(None,1,evaluate,click,MagicMock()),2)
        click.assert_not_called()

    def test_known_tour_closes_and_returns_to_flow(self):
        target={'x':100,'y':200,'label':'got it','text':'New upload features'}
        evaluate=MagicMock(side_effect=[target,None]);click=MagicMock(return_value=4)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dismiss_known_tours(None,1,evaluate,click,MagicMock()),5)
        click.assert_called_once_with(None,100,200,2)

    def test_unknown_target_metadata_never_clicks(self):
        click=MagicMock()
        dismiss_known_tours(None,1,MagicMock(return_value={'x':1,'y':2}),click,MagicMock())
        click.assert_not_called()

    def test_verified_backdrop_click_returns_to_flow(self):
        target={'x':12,'y':12,'label':'outside tour','text':'New editing features added'}
        click=MagicMock(return_value=4)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dismiss_known_tours(None,1,MagicMock(side_effect=[target,None]),click,MagicMock()),5)
        click.assert_called_once_with(None,12,12,2)

    def test_tour_that_does_not_close_is_not_clicked_repeatedly(self):
        target={'x':100,'y':200,'label':'got it','text':'New upload features'}
        click=MagicMock(return_value=4)
        with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(RuntimeError):
            dismiss_known_tours(None,1,MagicMock(return_value=target),click,MagicMock())
        click.assert_called_once()


if __name__=='__main__':unittest.main()
