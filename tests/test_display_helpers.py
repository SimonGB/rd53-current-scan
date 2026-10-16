from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from progress_estimate import ProgressEstimate,duration
from plot_style import signal_colors,sweep_style
from station import plot_mapped

class DisplayTests(unittest.TestCase):
    def test_estimate_refines_and_finalizes(self):
        e=ProgressEstimate(3,2,startup=5,finalization=2)
        self.assertEqual(e.remaining(0),13)
        e.start(10);self.assertEqual(e.remaining(12),11)
        e.point(17);e.point(21)
        self.assertEqual(e.remaining(21),6)
        self.assertEqual(e.remaining(23),4)
        e.point(25);self.assertIsNone(e.remaining(25))
        self.assertEqual(duration(125),'2m 05s')
    def test_shared_colors_distinct_direction(self):
        colors=signal_colors([dict(board='b1',testpoint='Vout',unit='V',direction=d) for d in ('up','down')])
        up=sweep_style('up',colors[('b1','Vout','V')]);down=sweep_style('down',colors[('b1','Vout','V')])
        self.assertEqual(up['color'],down['color']);self.assertEqual(down['linestyle'],':')
        self.assertNotEqual(up['marker'],down['marker'])
    def test_export_has_requested_styles(self):
        from matplotlib.figure import Figure
        rows=[dict(board='b1',testpoint='Vout',unit='V',direction=d,current_measured_A=.01,mean=1.8,capture_std=.001) for d in ('up','down')]
        with tempfile.TemporaryDirectory() as d,patch.object(Figure,'savefig',autospec=True) as save:
            plot_mapped(rows,Path(d));fig=save.call_args.args[0]
            a,b=fig.axes[0].lines
            self.assertEqual(a.get_color(),b.get_color())
            self.assertEqual(a.get_linestyle(),'-');self.assertEqual(b.get_linestyle(),':')
            self.assertEqual(b.get_marker(),'v')
