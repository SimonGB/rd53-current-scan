import unittest
import numpy as np
from probe_correction import scale_rows,waveform_view,factor

class ProbeCorrectionTests(unittest.TestCase):
    def profile(self):return {'backend':'pico','mappings':[{'board':'b','testpoint':'Vin','channel':'A','probe':1},{'board':'b','testpoint':'Vout','channel':'B','probe':10}]}
    def test_repeated_edits_and_reset(self):
        rows=[dict(board='b',testpoint='Vin',mean=.12,capture_std='.002',sample_std=.003,current_measured_A=.05)]
        scale_rows(rows,self.profile(),{'A':10});self.assertAlmostEqual(rows[0]['mean'],1.2);self.assertAlmostEqual(rows[0]['capture_std'],.02)
        scale_rows(rows,self.profile(),{'A':10});self.assertAlmostEqual(rows[0]['mean'],1.2)
        scale_rows(rows,self.profile(),{'A':1});self.assertAlmostEqual(rows[0]['mean'],.12);self.assertEqual(rows[0]['current_measured_A'],.05)
    def test_waveforms_original_untouched_and_recorded_factor_divided_out(self):
        records=[{'values':{'A':np.array([.1,.2]),'B':np.array([1.,2.])},'time_s':np.array([-.001,0])}]
        result=waveform_view(records,self.profile(),{'A':10,'B':1})
        np.testing.assert_allclose(result[0]['values']['A'],[1,2]);np.testing.assert_allclose(result[0]['values']['B'],[.1,.2])
        np.testing.assert_allclose(records[0]['values']['A'],[.1,.2]);np.testing.assert_array_equal(result[0]['time_s'],records[0]['time_s'])
    def test_invalid(self):
        for v in (0,-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):factor(v)
    def test_saved_correction_reloads_without_rewriting_csv(self):
        import tempfile,csv,json
        from pathlib import Path
        from sweep_plots import load_run,voltage_drops
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);profile=self.profile()
            (folder/'metadata.json').write_text(json.dumps({'profile':profile}))
            rows=[dict(board='b',testpoint=tp,unit='V',direction='up',point=0,current_measured_A=.05,mean=v) for tp,v in [('Vin',.2),('Vout',1.)]]
            with (folder/'measurements.csv').open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
            original=(folder/'measurements.csv').read_bytes()
            (folder/'probe_corrections.json').write_text(json.dumps({'A':10,'B':10}))
            loaded,_,_=load_run(folder)
            self.assertEqual(loaded[0]['mean'],2.);self.assertEqual(loaded[1]['mean'],1.)
            self.assertEqual((folder/'measurements.csv').read_bytes(),original)
