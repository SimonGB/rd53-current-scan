import csv
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
try:
    from PyQt6.QtWidgets import QApplication
except ImportError:
    QApplication = None


@unittest.skipUnless(QApplication, 'Optional GUI dependencies not installed')
class HistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_old_data_controls_export_and_live_isolation(self):
        from history_viewer import HistoryViewer
        from gui import Window
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)
            metadata=directory/'metadata.json'
            original=json.dumps({'settings':dict(plot_title='Archived run',plot_configuration='Old wiring',plot_max_ma=80)})
            metadata.write_text(original)
            rows=[dict(board=b,testpoint=tp,unit='V',direction=direction,point=i,
                       current_measured_A=.01*i,mean=mean)
                  for b in ('b1','b2') for tp,mean in [('Vin',2),('Vout',1.8)]
                  for direction in ('up','down') for i in range(3)]
            with (directory/'measurements.csv').open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
            live=Window();live.rows=[dict(rows[0],board='Live board')]
            live.plot_title.setText('Live title')
            with patch('gui.QFileDialog.getOpenFileName',return_value=(str(metadata),'')):
                live.open_history()
            viewer=live.history_windows[0]
            self.assertEqual(viewer.title.text(),'Archived run')
            self.assertEqual(len(viewer.plots[0][1].axes),2)
            viewer.show_configuration.setChecked(False)
            self.assertFalse(viewer.configuration.isEnabled())
            viewer.show_difference.setChecked(False)
            self.assertEqual(len(viewer.plots[0][1].axes),1)
            viewer.board.setCurrentIndex(1)
            self.assertEqual({r['board'] for r in viewer.visible_rows()},{'b1'})
            viewer.title.setText('Replotted')
            export=directory/'plots';export.mkdir()
            with patch('history_viewer.QFileDialog.getExistingDirectory',return_value=str(export)), patch('history_viewer.QMessageBox.information'):
                viewer.export()
            self.assertTrue((export/'mapped_voltages_up.pdf').is_file())
            self.assertTrue((export/'mapped_voltages_down.png').is_file())
            self.assertEqual(metadata.read_text(),original)
            self.assertEqual(live.plot_title.text(),'Live title')
            self.assertEqual(live.rows[0]['board'],'Live board')
            viewer.close();live.close()
