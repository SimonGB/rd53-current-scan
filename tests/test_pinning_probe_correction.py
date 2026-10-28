import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import copy
import unittest
import numpy as np
from PyQt6.QtWidgets import QApplication
from gui import Window

class PinningProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_pinning_changes_dc_and_startup_without_compounding(self):
        w=Window()
        try:
            p=w.snapshot();w.captured_profile=copy.deepcopy(p)
            m=p['mappings'][0]
            w.rows=[dict(board=m['board'],testpoint=m['testpoint'],input=m['channel'],unit='V',direction='up',point=0,current_measured_A=.05,mean=.12,capture_std=.001,sample_std=.002)]
            page=w.measurements_page.startup_page;page.profile=copy.deepcopy(p)
            page.records=[dict(current_ma=50,voltage_limit_v=2,repeat=1,time_s=np.array([-.001,0]),values={x['channel']:np.array([0.,.12]) for x in p['mappings']})]
            w.table.item(0,3).setText('10')
            self.assertAlmostEqual(w.rows[0]['mean'],1.2)
            self.assertEqual(page.probe_corrections[m['channel']],10)
            np.testing.assert_array_equal(page.records[0]['values'][m['channel']],[0,.12])
            w.table.item(0,3).setText('1')
            self.assertAlmostEqual(w.rows[0]['mean'],.12)
            self.assertEqual(page.probe_corrections[m['channel']],1)
            self.assertEqual(w.captured_profile['mappings'][0]['probe'],1)
        finally:w.close()
