import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scan import current_points, parser, run, SimSupply, SimScope


class Tests(unittest.TestCase):
    def test_grid(self):
        self.assertEqual(current_points(.01,.03,.01), [.01,.02,.03])
        self.assertEqual(current_points(.01,.035,.01), [.01,.02,.03])
        for values in [(0,1,.1),(.1,1,0),(1,.1,.1),(.1,float('nan'),.1)]:
            with self.assertRaises(ValueError): current_points(*values)

    def args(self, directory):
        return parser().parse_args(['--start','.01','--stop','.03','--step','.01',
            '--voltage-limit','1.5','--max-current','.03','--simulate',
            '--captures','2','--output',directory])

    def test_simulated_scan(self):
        import csv, json
        with tempfile.TemporaryDirectory() as d, patch('scan.plot'):
            a = self.args(d)
            a.reverse = True
            out = run(a)
            with (out/'results.csv').open() as f: rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),6)
            self.assertEqual(rows[-1]['direction'],'down')
            self.assertEqual(json.loads((out/'metadata.json').read_text())['status'],'complete')

    def test_shutdown_after_capture_failure(self):
        class Supply(SimSupply):
            off=False
            def output_off(self): self.off=True
        class Scope(SimScope):
            def capture(self): raise RuntimeError('capture failure')
        supply=Supply()
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(RuntimeError,'capture failure'):
                run(self.args(d), supply, Scope(supply))
        self.assertTrue(supply.off)

    def test_shutdown_after_interrupt(self):
        class Supply(SimSupply):
            off=False
            def output_off(self): self.off=True
        class Scope(SimScope):
            def capture(self): raise KeyboardInterrupt()
        supply=Supply()
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(KeyboardInterrupt): run(self.args(d), supply, Scope(supply))
        self.assertTrue(supply.off)

    def test_limits_before_connection(self):
        supply=SimSupply()
        with tempfile.TemporaryDirectory() as d:
            a=self.args(d)
            a.max_current=.02
            with self.assertRaises(ValueError): run(a,supply,SimScope(supply))
            self.assertFalse(hasattr(supply,'current'))


if __name__=='__main__': unittest.main()
