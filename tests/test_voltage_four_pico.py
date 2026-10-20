"""Regression checks for voltage axes, fixed GUI rows, and four Pico channels."""
import csv
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from matplotlib.figure import Figure
from mapping import default_profile, validate_profile
from scan import parser
from station import MappedPico, run_mapped
from sweep_plots import draw_sweep, load_run


def four_probe_profile():
    profile=default_profile()
    profile['boards'][0]['testpoints'] += [
        {'name':'Vin','x':.2,'y':.2}, {'name':'Iin','x':.4,'y':.2}
    ]
    for name, channel in [('Vin','C'),('Iin','D')]:
        profile['mappings'].append({
            'board':'board1','testpoint':name,
            'channel':channel,'range':2,'probe':10
        })
    return profile


class FourPicoTests(unittest.TestCase):
    def test_four_pico_inputs_validate(self):
        self.assertEqual(len(validate_profile(four_probe_profile())['mappings']),4)

    def test_five_pico_inputs_rejected(self):
        profile=four_probe_profile()
        profile['boards'][0]['testpoints'].append({'name':'Other','x':.6,'y':.2})
        profile['mappings'].append({
            'board':'board1','testpoint':'Other',
            'channel':'E','probe':1,'range':2
        })
        with self.assertRaises(ValueError):
            validate_profile(profile)

    def test_mapped_pico_configures_all_four(self):
        captured=[]
        class StubPico:
            resolution_bits=8
            sample_interval=1e-3
            def __init__(self,**kwargs):
                captured.append(kwargs)
            def connect(self):pass
            def close(self):pass
            def capture(self):
                return {name:[i,i+1] for i,name in enumerate('ABCD')}
        args=types.SimpleNamespace(resolution=12,sample_rate=1000,capture_time=.1)
        with patch.dict(sys.modules,{'picoscope':types.SimpleNamespace(PicoScope=StubPico)}):
            reader=MappedPico(args,four_probe_profile()['mappings'])
            result=reader.capture()
        self.assertEqual(set(captured[0]['channels']),set('ABCD'))
        self.assertEqual(captured[0]['channels']['D'],{'range':2,'probe':10})
        self.assertEqual(set(result),set('ABCD'))


class SweepModeTests(unittest.TestCase):
    def test_voltage_plot_uses_measured_voltage(self):
        rows=[{'board':'board1','testpoint':'Vout','unit':'V','direction':'up',
               'point':i,'sweep_mode':'voltage','voltage_measured_V':v,
               'current_measured_A':.01+i*.01,'mean':1.8}
              for i,v in enumerate((.4,.7,1.2))]
        fig=Figure()
        draw_sweep(fig,rows,'up','Voltage sweep',maximum=2,show_difference=False)
        self.assertIn('voltage',fig.axes[0].get_xlabel().lower())
        self.assertEqual(list(fig.axes[0].lines[0].get_xdata()),[.4,.7,1.2])
        draw_sweep(fig,[], 'up', 'Empty voltage sweep',maximum=2,
                   mode_hint='voltage',show_difference=False)
        self.assertIn('voltage',fig.axes[0].get_xlabel().lower())

    def test_voltage_sweep_metadata_and_reopen(self):
        args=parser().parse_args([
            '--simulate','--sweep-mode','voltage','--start','.1','--stop','.3',
            '--step','.1','--voltage-limit','1.5','--max-current','.45',
            '--captures','2'])
        with tempfile.TemporaryDirectory() as tmp:
            args.output=Path(tmp)
            with patch('station.plot_mapped'):
                out=run_mapped(args,default_profile())
            rows, settings, _=load_run(out)
            self.assertEqual(settings['sweep_mode'],'voltage')
            self.assertEqual(len(rows),6)
            self.assertTrue(all(r['sweep_mode']=='voltage' for r in rows))
            self.assertEqual(sorted(set(r['voltage_measured_V'] for r in rows)),[.1,.2,.3])


os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
try:
    from PyQt6.QtWidgets import QApplication
except ImportError:
    QApplication=None


@unittest.skipUnless(QApplication, 'PyQt6 is optional for non-GUI tests')
class StableGuiRowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_mode_switch_keeps_same_controls_and_remembers_values(self):
        from gui import Window
        w=Window()
        try:
            fields=[w.fields[key] for key in ('start','stop','step')]
            before=[w.scan_form.getWidgetPosition(x)[0] for x in fields]
            w.sweep_mode.setCurrentText('Voltage')
            after=[w.scan_form.getWidgetPosition(x)[0] for x in fields]
            self.assertEqual(before,after)
            self.assertAlmostEqual(w.fields['stop'].value(),1.5)
            self.assertIn('[V]',w.scan_form.labelForField(w.fields['stop']).text())
            w.fields['stop'].setValue(1.7)
            w.sweep_mode.setCurrentText('Current')
            self.assertAlmostEqual(w.fields['stop'].value(),50.0)
            w.sweep_mode.setCurrentText('Voltage')
            self.assertAlmostEqual(w.fields['stop'].value(),1.7)
        finally:
            w.close()


if __name__=='__main__':
    unittest.main()
