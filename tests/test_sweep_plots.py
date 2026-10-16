from pathlib import Path
import tempfile
import unittest
from matplotlib.figure import Figure
from sweep_plots import draw_sweep, export_sweeps


class SweepPlotTests(unittest.TestCase):
    def rows(self):
        return [dict(board=f'board{b}', testpoint=tp, unit='V', direction=d,
                     point=p, current_measured_A=current, mean=1.8)
                for b in range(1, 6) for tp in ('Vin', 'Vout')
                for d in ('up', 'down')
                for p, current in enumerate((.01, .05, .15))]

    def test_split_range_colors_signals_and_legend(self):
        rows = self.rows()
        figures = []
        for direction in ('up', 'down'):
            fig = Figure(); draw_sweep(fig, rows, direction, '5 RD53b in parallel')
            ax = fig.axes[0]; figures.append(fig)
            self.assertEqual(len(ax.lines), 10)
            self.assertEqual(ax.get_xlim(), (0, 100))
            for i in range(0, 10, 2):
                vin, vout = ax.lines[i:i+2]
                self.assertEqual(len(vin.get_xdata()), 2)
                self.assertEqual(vin.get_color(), vout.get_color())
                self.assertEqual(vin.get_linestyle(), ':')
                self.assertEqual(vout.get_linestyle(), '-')
                self.assertNotEqual(vin.get_marker(), vout.get_marker())
            self.assertEqual(len(set(l.get_color() for l in ax.lines)), 5)
            labels = [t.get_text() for t in ax.get_legend().get_texts()]
            self.assertEqual(labels[:5], [f'board{b}: Vin' for b in range(1, 6)])
            self.assertEqual(labels[5:], [f'board{b}: Vout' for b in range(1, 6)])
        self.assertEqual([l.get_color() for l in figures[0].axes[0].lines],
                         [l.get_color() for l in figures[1].axes[0].lines])

    def test_export_and_optional_down(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            export_sweeps([r for r in self.rows() if r['direction'] == 'up'], out)
            self.assertEqual({p.name for p in out.iterdir()},
                             {'mapped_voltages_up.pdf', 'mapped_voltages_up.png'})
            export_sweeps(self.rows(), out)
            self.assertTrue((out/'mapped_voltages_down.pdf').is_file())

    def test_units_not_mixed(self):
        rows=self.rows()
        rows.append(dict(rows[0], unit='A', testpoint='Iin', mean=.001))
        fig=Figure();draw_sweep(fig,rows,'up','Mixed measurements')
        self.assertEqual([a.get_ylabel() for a in fig.axes], ['Voltage (V)', 'Current (A)'])

    def test_configuration_box_and_hide(self):
        text = '3 serial domains: Boards (1 & 2) → (3 & 4) → 5'
        for direction in ('up', 'down'):
            fig=Figure()
            draw_sweep(fig,self.rows(),direction,'5 RD53b',configuration=text)
            label=next(t for t in fig.axes[0].texts if t.get_text()==text)
            self.assertIsNotNone(label.get_bbox_patch())
            draw_sweep(fig,self.rows(),direction,'5 RD53b',configuration='')
            self.assertFalse(any(t.get_text()==text for t in fig.axes[0].texts))

    def test_dark_live_style_leaves_exports_white(self):
        from matplotlib.colors import to_hex
        fig=Figure()
        draw_sweep(fig,self.rows(),'up','Live',configuration='Test',dark=True)
        self.assertEqual(to_hex(fig.axes[0].get_facecolor()), '#101f2e')
        self.assertEqual(to_hex(fig.axes[0].xaxis.label.get_color()), '#c6d8e9')
        draw_sweep(fig,self.rows(),'up','Export')
        self.assertEqual(to_hex(fig.axes[0].get_facecolor()), '#ffffff')

    def test_replot_reads_saved_settings_and_overrides_visibility(self):
        import csv
        import json
        from unittest.mock import patch
        from sweep_plots import main
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)
            metadata=directory/'metadata.json'
            metadata.write_text(json.dumps({'settings':dict(plot_title='My run',
                plot_configuration='Saved wiring',plot_hide_configuration=True,plot_max_ma=80)}))
            with (directory/'measurements.csv').open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=list(self.rows()[0]))
                writer.writeheader();writer.writerows(self.rows())
            with patch('sys.argv',['sweep_plots.py',str(metadata)]), patch('sweep_plots.export_sweeps') as export:
                main()
                self.assertEqual(export.call_args.args[2:],('My run',80,''))
            with patch('sys.argv',['sweep_plots.py',str(directory),'--configuration','New wiring','--max-ma','100']), patch('sweep_plots.export_sweeps') as export:
                main()
                self.assertEqual(export.call_args.args[2:],('My run',100,'New wiring'))
