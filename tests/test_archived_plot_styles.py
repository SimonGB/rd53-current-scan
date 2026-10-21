"""Archived plot styling should be reversible, reproducible and non-destructive."""
import csv
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PyQt6.QtWidgets import QApplication
except ImportError:
    QApplication = None

@unittest.skipUnless(QApplication,'PyQt6 is optional')
class ArchivedStylesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def create_run(self, directory):
        metadata=directory/'metadata.json'
        original=json.dumps({'settings':{'plot_title':'Original','plot_max_ma':100}})
        metadata.write_text(original)
        rows=[{'board':'board1','testpoint':tp,'unit':'V',
               'direction':direction,'point':i,
               'current_measured_A':.01+i*.01,'mean':1.5}
              for direction in ('up','down') for tp in ('Vin','Vout') for i in range(2)]
        with (directory/'measurements.csv').open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return original

    def test_customize_save_reopen_and_restore_auto(self):
        from history_viewer import HistoryViewer
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            original=self.create_run(folder)
            original_csv=(folder/'measurements.csv').read_bytes()
            viewer=HistoryViewer(folder)
            self.assertEqual(viewer.plotting.rowCount(),2)
            self.assertEqual(viewer.plotting.item(0,3).text(),'Auto')
            with patch('history_viewer.edit_style',return_value={
                'color':'#ff0000','linestyle':'--','marker':'D'}):
                viewer.edit_plotting(0,3)
            point=viewer.style_targets[0]
            records=[r for r in viewer.rows if
                     (r['board'],r['testpoint'],r['unit'])==point]
            self.assertTrue(all(r['color']=='#ff0000' for r in records))
            self.assertEqual(viewer.plots[0][1].axes[0].lines[0].get_linestyle(),'--')
            viewer.title.setText('Updated plot')
            viewer.maximum.setValue(60)
            with patch('history_viewer.QFileDialog.getExistingDirectory',return_value=str(folder)), \
                 patch('history_viewer.QMessageBox.question',return_value=__import__('PyQt6').QtWidgets.QMessageBox.StandardButton.Yes), \
                 patch('history_viewer.QMessageBox.information'):
                viewer.export()
            self.assertEqual((folder/'metadata.json').read_text(),original)
            self.assertEqual((folder/'measurements.csv').read_bytes(),original_csv)
            presentation=json.loads((folder/'plot_presentation.json').read_text())
            self.assertEqual(presentation['settings']['plot_max_ma'],60)
            self.assertEqual(presentation['styles'][0]['marker'],'D')
            viewer.close()
            reopened=HistoryViewer(folder)
            self.assertEqual(reopened.title.text(),'Updated plot')
            self.assertEqual(reopened.maximum.value(),60)
            self.assertEqual(reopened.rows[0]['color'],'#ff0000')
            with patch('history_viewer.edit_style',return_value={
                'color':'','linestyle':'','marker':''}):
                reopened.edit_plotting(0,3)
            self.assertEqual(reopened.plotting.item(0,3).text(),'Auto')
            reopened.close()

if __name__=='__main__':
    unittest.main()
