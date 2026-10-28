import csv,json,tempfile,unittest
from pathlib import Path
import numpy as np
from matplotlib.figure import Figure
from measurement_comparison import load_measurement,compatible,draw_comparison

class ComparisonTests(unittest.TestCase):
    def dc(self,path,value,mode='current'):
        path.mkdir();profile={'backend':'pico','mappings':[dict(board='board1',testpoint='Vin',channel='A',probe=1)]}
        (path/'metadata.json').write_text(json.dumps({'profile':profile,'settings':{'sweep_mode':mode}}))
        rows=[dict(board='board1',testpoint='Vin',unit='V',direction='up',point=i,current_measured_A=.01*(i+1),voltage_measured_V=.5*(i+1),mean=value) for i in range(2)]
        with (path/'measurements.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    def test_overlay_preserves_sources_and_applies_probe_correction(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);self.dc(p/'first',.1);self.dc(p/'second',.2)
            original=(p/'first'/'measurements.csv').read_bytes()
            (p/'first'/'probe_corrections.json').write_text('{"A":10}')
            a=load_measurement(p/'first');b=load_measurement(p/'second');compatible([a],b)
            figure=Figure();draw_comparison(figure,[a,b]);self.assertEqual(len(figure.axes[0].lines),2)
            np.testing.assert_allclose(figure.axes[0].lines[0].get_ydata(),[1,1]);self.assertEqual((p/'first'/'measurements.csv').read_bytes(),original)
    def test_incompatible_axis_types_rejected(self):
        with self.assertRaises(ValueError):compatible([{'kind':'dc_current'}],{'kind':'dc_voltage'})
        with self.assertRaises(ValueError):compatible([{'kind':'startup'}],{'kind':'dc_current'})
    def test_startup_loader_uses_original_time_and_corrected_amplitude(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);np.savez(p/'capture.npz',time_s=np.array([-.001,0,.004]),A=np.array([0,.1,.2]))
            profile={'backend':'pico','boards':[{'id':'b','testpoints':[{'name':'Vref','color':'#0000ff'}]}],'mappings':[dict(board='b',testpoint='Vref',channel='A',probe=1)]}
            (p/'metadata.json').write_text(json.dumps({'profile':profile,'settings':{},'captures':[dict(current_ma=50,voltage_limit_v=2,repeat=1,file='capture.npz')]}));(p/'probe_corrections.json').write_text('{"A":10}')
            loaded=load_measurement(p);np.testing.assert_allclose(loaded['series'][0]['x'],[-1,0,4]);np.testing.assert_allclose(loaded['series'][0]['y'],[0,1,2])

import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
try:
    from PyQt6.QtWidgets import QApplication
except ImportError:QApplication=None

@unittest.skipUnless(QApplication,'Optional GUI dependencies')
class ComparisonGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_add_hide_label_and_export(self):
        from unittest.mock import patch
        from PyQt6.QtCore import Qt
        from measurement_comparison_gui import MeasurementComparison
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);helper=ComparisonTests();helper.dc(p/'first',.1);helper.dc(p/'second',.2)
            dialog=MeasurementComparison()
            try:
                dialog.append_run(p/'first');dialog.append_run(p/'second')
                self.assertEqual(len(dialog.figure.axes[0].lines),2)
                dialog.table.item(0,1).setText('GSI board');dialog.table.item(1,0).setCheckState(Qt.CheckState.Unchecked)
                self.assertEqual(len(dialog.figure.axes[0].lines),1);self.assertIn('GSI board',dialog.figure.axes[0].lines[0].get_label())
                from measurement_comparison import series_key
                dialog.runs[0]['series'][0]['display_color']='#ff00ff';dialog.legend_size.setValue(15);dialog.font_size.setValue(18);dialog.line_width.setValue(2.5)
                dialog.redraw();self.assertEqual(dialog.figure.axes[0].lines[0].get_color(),'#ff00ff')
                self.assertEqual(dialog.figure.axes[0].get_legend().get_texts()[0].get_fontsize(),15)
                with patch('measurement_comparison_gui.QFileDialog.getSaveFileName',return_value=(str(p/'overlay.png'),'')):dialog.export()
                self.assertTrue((p/'overlay.pdf').exists());manifest=json.loads((p/'overlay.json').read_text());self.assertEqual(manifest['runs'][0]['label'],'GSI board')
                self.assertEqual(manifest['appearance']['legend_size'],15)
                self.assertEqual(manifest['runs'][0]['colors'][series_key(dialog.runs[0]['series'][0])],'#ff00ff')
                with patch('measurement_comparison_gui.QFileDialog.getOpenFileName',return_value=(str(p/'overlay.json'),'')):dialog.open_comparison()
                self.assertEqual(dialog.legend_size.value(),15)
                self.assertEqual(dialog.figure.axes[0].lines[0].get_color(),'#ff00ff')
            finally:dialog.close()
