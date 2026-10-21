"""Rext-comparison integration checks using synthetic, local run folders."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from rext_compare import (
    parse_configuration, discover, families, signal_choices, trace,
    fit_trace, compare, save_comparison,
)


def make_run(root, name, rext, rref=150, slope=0.7):
    folder = root / 'board1' / name
    folder.mkdir(parents=True, exist_ok=True)
    configuration = f'board #1 in SLDO mode - Rref = {rref}kOhm, Rext = {rext} Ohm'
    metadata = {'settings': {'plot_configuration': configuration,
                              'sweep_mode': 'current', 'name': 'board1'}}
    (folder/'metadata.json').write_text(json.dumps(metadata))
    rows = []
    for direction in ('up','down'):
        for i, x in enumerate(np.arange(.01, .10, .01)):
            for signal in ('Vin','Vout'):
                rows.append(dict(board='board1',testpoint=signal,unit='V',
                                 direction=direction,point=i,current_measured_A=float(x),
                                 mean=float(slope*x + .6 if signal == 'Vin' else .8),
                                 sweep_mode='current'))
    with (folder/'measurements.csv').open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return folder


class RextCompareTests(unittest.TestCase):
    def test_configuration_parser(self):
        parsed=parse_configuration('board #1 in SLDO mode - Rref = 150kOhm, Rext = 750 Ohm')
        self.assertEqual(parsed[0],750)
        other=parse_configuration('board #1 in SLDO mode - Rref = 150kOhm, Rext=0.9 kΩ')
        self.assertEqual(other[0],900)
        self.assertEqual(parsed[1],other[1])
        self.assertIsNone(parse_configuration('board1 without resistance'))

    def test_group_overlay_fit_and_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            folders=[]
            for value,slope in [(600,.65),(750,.75),(900,.85)]:
                folders.append(make_run(root,f'20261021T10000{value}',value,slope=slope))
            different=make_run(root,'different_reference',650,rref=200)
            scans,errors=discover(root,return_errors=True)
            self.assertEqual(len(scans),4)
            groups=families(scans)
            self.assertEqual(len(groups),2)
            selected=next(group for group in groups.values() if len(group)==3)
            self.assertEqual(signal_choices(selected),['Vin','Vout'])
            x,y=trace(selected[0],'Vin')
            self.assertEqual(len(x),9)
            self.assertEqual(len(y),9)
            fit=fit_trace(x,y,min_ma=10,max_ma=90,max_voltage=2)
            self.assertAlmostEqual(fit['slope_ohm'],.65,places=5)
            self.assertAlmostEqual(fit['offset_v'],.6,places=5)
            figure, fits=compare(selected,'Vin',fit=True,fit_min_ma=10,
                                 fit_max_ma=90,fit_max_voltage=2)
            self.assertEqual(len(fits),3)
            self.assertEqual(len(figure.axes[0].lines),6)
            self.assertEqual(figure.axes[0].get_xlabel(),'Measured input current (mA)')
            files_before={d:(d/'measurements.csv').read_bytes() for d in folders}
            dest=root/'published'
            save_comparison(figure,fits,dest)
            for filename in ('rext_comparison.png','rext_comparison.pdf','rext_fit_results.csv'):
                self.assertTrue((dest/filename).is_file(),filename)
            for d,old in files_before.items():
                self.assertEqual((d/'measurements.csv').read_bytes(),old)

    def test_insufficient_fit_points_rejected(self):
        x=np.array([.001,.002,.003])
        y=np.array([.5,.6,2.3])
        with self.assertRaisesRegex(ValueError,'3 distinct'):
            fit_trace(x,y,min_ma=1,max_ma=3,max_voltage=2)

    def test_presentation_sidecar_does_not_change_original_group(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            folder=make_run(root,'test',750)
            (folder/'plot_presentation.json').write_text(
                json.dumps({'settings': {'plot_configuration': 'something else'}}))
            scans=discover(root)
            self.assertEqual(len(scans),1)
            self.assertEqual(scans[0].rext_ohms,750)

    def test_gui_initialization_with_empty_directory(self):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        try:
            from PyQt6.QtWidgets import QApplication
            from rext_compare_gui import RextCompareWindow
        except ImportError:
            self.skipTest('PyQt6 not installed')
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            window = RextCompareWindow(temp)
            try:
                self.assertIsNotNone(window.figure)
                self.assertEqual(window.runs.rowCount(), 0)
                self.assertIsNone(window.last_figure)
                window.plot_limit.setValue(150)
                window.fit_end.setValue(95)
            finally:
                window.close()

    def test_gui_initialization_with_matching_runs(self):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        try:
            from PyQt6.QtWidgets import QApplication
            from rext_compare_gui import RextCompareWindow
        except ImportError:
            self.skipTest('PyQt6 not installed')
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_run(root, '001', 600)
            make_run(root, '002', 750)
            window = RextCompareWindow(root)
            try:
                self.assertEqual(window.runs.rowCount(), 2)
                self.assertIsNotNone(window.last_figure)
                self.assertEqual(len(window.figure.axes[0].lines), 2)
            finally:
                window.close()


if __name__=='__main__':
    unittest.main()
