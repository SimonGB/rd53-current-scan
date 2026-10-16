import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
try:
    from PyQt6.QtWidgets import QApplication,QPlainTextEdit,QPushButton,QDialog,QComboBox,QDialogButtonBox
except ImportError:
    QApplication=None


@unittest.skipUnless(QApplication,'Optional GUI dependencies not installed')
class AbortGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_stop_before_reason_and_no_dismissal(self):
        from gui import Window
        from run_comments import RunComments
        w=Window();notes=RunComments()
        with tempfile.TemporaryDirectory() as d:
            notes.bind(d)
            w.worker=SimpleNamespace(stop=threading.Event(),notes=notes,isRunning=lambda:False)
            w.running=True;w.stop()
            self.assertTrue(w.worker.stop.is_set())
            dialog=w.abort_dialog;dialog.reject()
            self.assertTrue(dialog.isVisible())
            edit=dialog.findChild(QPlainTextEdit);button=dialog.findChild(QPushButton)
            self.assertFalse(button.isEnabled())
            w.finished('Interrupted',False)
            self.assertFalse(w.startbtn.isEnabled())
            notes.finalize({'status':'interrupted'})
            edit.setPlainText('   ');self.assertFalse(button.isEnabled())
            edit.setPlainText('Board 2 needs checking');button.click()
            self.assertIsNone(w.abort_dialog);self.assertTrue(w.startbtn.isEnabled())
            self.assertEqual(json.loads((Path(d)/'metadata.json').read_text())['abort']['reason'],'Board 2 needs checking')
        w.worker=None;w.close()

    def test_add_connection_from_dialog_and_save_profile(self):
        from gui import Window
        from connections import connection_points
        from mapping import validate_profile
        w=Window();w.backend.setCurrentText('venelin')
        def choose(dialog):
            combos=dialog.findChildren(QComboBox)
            positive,negative,channel=combos
            positive.setCurrentIndex(next(i for i in range(positive.count()) if positive.itemData(i)==dict(board='board1',point='Vout')))
            negative.setCurrentIndex(0)
            dialog.findChild(QDialogButtonBox).accepted.emit()
            return QDialog.DialogCode.Accepted
        with patch.object(QDialog,'exec',choose):w.add_connection_dialog()
        p=validate_profile(json.loads(json.dumps(w.snapshot())))
        self.assertEqual(len(connection_points(p)),1)
        self.assertIn('Connections',w.overview.cards)
        w.boardselect.setCurrentText('Connections');w.showall.setChecked(True)
        self.assertIn('Global / GND',w.table.item(w.table.rowCount()-1,1).text())
        w.close()
