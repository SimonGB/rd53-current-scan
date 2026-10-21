"""Rext-comparison integration checks using synthetic, local run folders."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from rext_compare import (
    parse_configuration, parse_variable, discover, families, signal_choices, trace,
    fit_trace, compare, plot_fit_parameters, save_comparison,
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
            self.assertIn('slope_error_ohm',fit)
            self.assertIn('offset_error_v',fit)
            figure, fits=compare(selected,'Vin',fit=True,fit_min_ma=10,
                                 fit_max_ma=90,fit_max_voltage=2)
            self.assertEqual(len(fits),3)
            self.assertEqual(len(figure.axes[0].lines),6)
            summary = plot_fit_parameters(fits)
            self.assertEqual(len(summary.axes),2)
            self.assertIn('slope',summary.axes[0].get_ylabel())
            self.assertIn('ofs',summary.axes[1].get_ylabel())
            self.assertEqual(len(summary.axes[0].containers),1)
            self.assertEqual(len(summary.axes[0].containers[0].lines[0].get_xdata()),3)
            self.assertEqual(figure.axes[0].get_xlabel(),'Measured input current (mA)')
            files_before={d:(d/'measurements.csv').read_bytes() for d in folders}
            dest=root/'published'
            save_comparison(figure,fits,dest)
            for filename in ('rext_comparison.png','rext_comparison.pdf',
                             'rext_fit_parameters.png','rext_fit_parameters.pdf',
                             'rext_fit_results.csv'):
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
                self.assertEqual(len(window.parameters_figure.axes),2)
                self.assertEqual(len(window.figure.axes[0].lines), 2)
            finally:
                window.close()

    def test_parameter_figure_renders_mathtext_labels(self):
        """Regression for malformed double-backslash \\mathrm axis labels."""
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        fits = [dict(rext_ohm=750, run='test', status='ok',
                     slope_ohm=10.0, slope_error_ohm=.2,
                     offset_v=1.5, offset_error_v=.03)]
        figure = plot_fit_parameters(fits)
        FigureCanvasAgg(figure).draw()
        self.assertIn('slope', figure.axes[0].get_ylabel())

    def test_parameter_figure_skips_failed_fits_and_keeps_repeated_rext(self):
        entries = [
            dict(rext_ohm=600, run='first', status='ok', slope_ohm=1.2,
                 slope_error_ohm=.1, offset_v=1.8, offset_error_v=.02),
            dict(rext_ohm=600, run='second', status='ok', slope_ohm=1.4,
                 slope_error_ohm=.2, offset_v=1.7, offset_error_v=.03),
            dict(rext_ohm=900, run='third', status='not enough points')
        ]
        from matplotlib.figure import Figure
        canvas = Figure()
        result = plot_fit_parameters(entries, figure=canvas)
        self.assertIs(result,canvas)
        self.assertEqual(len(result.axes),2)
        self.assertEqual(list(result.axes[0].containers[0].lines[0].get_xdata()),[600,600])
        self.assertEqual(list(result.axes[1].containers[0].lines[0].get_ydata()),[1.8,1.7])

    def test_linear_parameter_uncertainties_finite(self):
        currents = np.linspace(.01,.08,15)
        values = 4.2 * currents + .8 + .004 * np.sin(np.arange(len(currents)))
        fit = fit_trace(currents, values, min_ma=10, max_ma=80)
        self.assertAlmostEqual(fit['slope_ohm'],4.2,delta=.1)
        self.assertGreater(fit['slope_error_ohm'],0)
        self.assertGreater(fit['offset_error_v'],0)

    def test_plot_axis_maximum_does_not_change_fit_parameters(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_run(root,'001',750,slope=1.25)
            runs = discover(root)
            _, full = compare(runs,'Vin',fit=True,fit_min_ma=10,fit_max_ma=90,
                              max_plot_ma=100)
            _, clipped = compare(runs,'Vin',fit=True,fit_min_ma=10,fit_max_ma=90,
                                 max_plot_ma=30)
            self.assertEqual(full[0]['status'],'ok')
            self.assertEqual(clipped[0]['status'],'ok')
            self.assertAlmostEqual(full[0]['slope_ohm'],clipped[0]['slope_ohm'])
            self.assertAlmostEqual(full[0]['offset_v'],clipped[0]['offset_v'])

    def test_offset_voltage_parser_and_fixed_rext_grouping(self):
        first = 'board #1 in SLDO mode - Rref = 150kOhm, Rext = 750 Ohm, Voffset = 0.5 V'
        second = 'board #1 in SLDO mode - Rref = 150kOhm, Rext = 750 Ohm, Voffset = 750 mV'
        different_rext = 'board #1 in SLDO mode - Rref = 150kOhm, Rext = 900 Ohm, Voffset = 0.5 V'
        result1 = parse_variable(first, 'offset')
        result2 = parse_variable(second, 'offset')
        self.assertAlmostEqual(result1[0], .5)
        self.assertAlmostEqual(result2[0], .75)
        self.assertEqual(result1[1], result2[1])
        self.assertNotEqual(result1[1], parse_variable(different_rext,'offset')[1])
        self.assertEqual(parse_variable('Voffset = -0.15 V','offset')[0], -.15)
        self.assertIsNone(parse_variable('Rext = 750 Ohm','offset'))

    def test_offset_voltage_overlay_and_parameter_graph(self):
        from rext_compare import plot_fit_parameters
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index, offset in enumerate((0.4, .6, .8)):
                folder = make_run(root, str(index), 750, slope=1.0+offset)
                meta = folder / 'metadata.json'
                data = json.loads(meta.read_text())
                data['settings']['plot_configuration'] += f', Voffset = {offset:g} V'
                meta.write_text(json.dumps(data))
            other = make_run(root, 'other_rext', 900, slope=1.8)
            meta = other / 'metadata.json'
            data = json.loads(meta.read_text())
            data['settings']['plot_configuration'] += ', Voffset = 0.6 V'
            meta.write_text(json.dumps(data))
            scans = discover(root, parameter='offset')
            self.assertEqual(len(scans),4)
            groups = families(scans)
            self.assertEqual(len(groups),2)
            selected = next(group for group in groups.values() if len(group)==3)
            fig, fit_results = compare(selected,'Vin',fit=True,
                                       fit_min_ma=10,fit_max_ma=90)
            self.assertEqual(len(fig.axes[0].lines),6)
            self.assertEqual(sorted(r['offset_setting_v'] for r in fit_results),
                             [.4,.6,.8])
            summary = plot_fit_parameters(fit_results)
            self.assertIn('Configured offset voltage', summary.axes[-1].get_xlabel())
            FigureCanvasAgg(summary).draw()
            dest=root/'comparison_output'
            save_comparison(fig,fit_results,dest,selected)
            self.assertTrue((dest/'offset_setting_comparison.pdf').exists())
            self.assertTrue((dest/'offset_setting_fit_parameters.pdf').exists())
            self.assertTrue((dest/'offset_setting_fit_results.csv').exists())
            self.assertFalse((dest/'rext_comparison.pdf').exists())

    def test_offset_voltage_gui_switches_parameter(self):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        try:
            from PyQt6.QtWidgets import QApplication
            from rext_compare_gui import RextCompareWindow
        except ImportError:
            self.skipTest('PyQt6 not installed')
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            folder=make_run(root,'001',750)
            meta=folder/'metadata.json'
            data=json.loads(meta.read_text())
            data['settings']['plot_configuration'] += ', Voffset = 0.7 V'
            meta.write_text(json.dumps(data))
            window=RextCompareWindow(root)
            try:
                self.assertEqual(window.vary.currentData(),'rext')
                window.vary.setCurrentIndex(window.vary.findData('offset'))
                self.assertEqual(window.runs.rowCount(),1)
                self.assertIn('Voffset',window.runs.horizontalHeaderItem(1).text())
                self.assertIsNotNone(window.last_figure)
                self.assertIn('Configured offset voltage',
                              window.parameters_figure.axes[-1].get_xlabel())
            finally:
                window.close()

    def test_dynamic_parameter_discovery_and_vofshalf_grouping(self):
        from config_parameters import discovered_parameters, extract_assignments
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        example = ('board #1 in SLDO mode - Rref = 150kOhm, '
                   'Rext = 750 Ohm, Vofshalf = 400 mV')
        data = {item['name']: item for item in extract_assignments(example)}
        self.assertEqual(set(data), {'rref', 'rext', 'vofshalf'})
        self.assertEqual(data['rref']['value'],150000)
        self.assertEqual(data['vofshalf']['value'],.4)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for index, (rext, value, unit) in enumerate(
                    [(750,400,'mV'),(750,.6,'V'),(750,.8,'V'),(900,.5,'V')]):
                folder=make_run(root,str(index),rext,slope=1+index*.1)
                meta=folder/'metadata.json'
                metadata=json.loads(meta.read_text())
                metadata['settings']['plot_configuration'] += f', Vofshalf = {value:g} {unit}'
                meta.write_text(json.dumps(metadata))
            proposed=discovered_parameters(root)
            labels={key:(label,unit,count) for key,label,unit,count in proposed}
            self.assertEqual(labels['vofshalf'],('Vofshalf','V',4))
            self.assertIn('rref',labels)
            self.assertIn('rext',labels)
            runs=discover(root,parameter='vofshalf')
            self.assertEqual(len(runs),4)
            grouped=families(runs)
            self.assertEqual(len(grouped),2)
            chosen=next(group for group in grouped.values() if len(group)==3)
            self.assertEqual([run.variable_value for run in chosen],[.4,.6,.8])
            figure,fits=compare(chosen,'Vin',fit=True,fit_min_ma=10,fit_max_ma=90)
            self.assertEqual([entry['variable_value'] for entry in fits],[.4,.6,.8])
            summary=plot_fit_parameters(fits)
            self.assertEqual(summary.axes[-1].get_xlabel(),'vofshalf (V)')
            FigureCanvasAgg(summary).draw()
            output=root/'summary'
            save_comparison(figure,fits,output,chosen)
            self.assertTrue((output/'vofshalf_comparison.pdf').is_file())
            self.assertTrue((output/'vofshalf_fit_parameters.pdf').is_file())
            with (output/'vofshalf_fit_results.csv').open() as fh:
                self.assertIn('variable_value',fh.readline())

    def test_dynamic_parameter_gui_suggestions(self):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        try:
            from PyQt6.QtWidgets import QApplication
            from rext_compare_gui import RextCompareWindow
        except ImportError:
            self.skipTest('PyQt6 is optional')
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for index, value in enumerate((.4,.6)):
                folder=make_run(root,str(index),750)
                meta=folder/'metadata.json'
                data=json.loads(meta.read_text())
                data['settings']['plot_configuration'] += f', Vofshalf = {value} V'
                meta.write_text(json.dumps(data))
            viewer=RextCompareWindow(root)
            try:
                index=viewer.vary.findData('vofshalf')
                self.assertGreater(index,0)
                self.assertIn('Vofshalf',viewer.vary.itemText(index))
                viewer.vary.setCurrentIndex(index)
                self.assertEqual(viewer.runs.rowCount(),2)
                self.assertIn('Vofshalf',viewer.runs.horizontalHeaderItem(1).text())
                self.assertEqual(len(viewer.parameters_figure.axes),2)
            finally:
                viewer.close()


if __name__=='__main__':
    unittest.main()
