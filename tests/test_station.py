import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import numpy as np
from scan import parser,SimSupply
from station import run_mapped,SimMapped,summaries
from mapping import default_profile,validate_profile
from venelin import decode_file,input_profiles,Venelin

class StationTests(unittest.TestCase):
    def args(self,path):
        return parser().parse_args(['--simulate','--start','.005','--stop','.01','--step','.005',
            '--voltage-limit','3.5','--max-current','.45','--output',path,'--captures','3','--reverse'])
    def test_adc_helper_masks_and_fresh_records(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);cfg=root/'config.toml';cfg.write_text('[Calibration]\nPrecisionBits=24\n')
            data=root/'fadc.dat';calls=[]
            reader=Venelin(root,cfg,data,['PMOD1_Ch0','PMOD5_Ch0'],1000,.002,threading.Event())
            def command(name,args,timeout):
                calls.append((name,args))
                if name=='pm_adcs_startl':
                    data.write_text('# p_nr | tstmp | ADC_CH0(PMOD1_dV0) | ADC_CH32(PMOD5_dV0) |\n0 0x1 4194304 2097152\n1 0x2 4194304 2097152\n')
            reader.command=command;reader.connect();values=reader.capture()
            self.assertEqual(calls[0][0],'pm_adcs_quick_ini')
            args=calls[1][1]
            self.assertEqual(args[args.index('--fadc-mask-0-31')+1],'0x1')
            self.assertEqual(args[args.index('--fadc-mask-32-47')+1],'0x1')
            self.assertEqual(args[args.index('--adc24-flag')+1],'0x1')
            np.testing.assert_allclose(values['PMOD5_Ch0'],[.6,.6])
            reader.command=lambda *args:None
            with self.assertRaisesRegex(RuntimeError,'fresh data'):reader.capture()

    def test_confirmed_rd53_types_and_voltage_scaling(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=Path(d)/'config.toml';cfg.write_text('[Calibration]\nPrecisionBits=24\n')
            profiles=input_profiles(cfg)
            for pmod in range(1,7):
                self.assertEqual(profiles[f'PMOD{pmod}_Ch0']['kind'],'I')
                self.assertEqual(profiles[f'PMOD{pmod}_Ch3']['kind'],'dV' if pmod<=2 else 'I')
            data=Path(d)/'fadc.dat'
            data.write_text('# ADC_CH3(PMOD1_I3)\n0 0x1 4194304\n1 0x2 4194304\n')
            values,_=decode_file(data,['PMOD1_Ch3'],profiles)
            np.testing.assert_allclose(values['PMOD1_Ch3'],[1.8,1.8])

    def test_saved_pmod_gui_setup_overrides_toml(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=Path(d)/'config.toml'
            cfg.write_text('[BoardInputs.PMOD1]\nKinds=["dV","dV","dV","I","I","I","aV","aV"]\n')
            profiles=input_profiles(cfg)
            self.assertEqual(profiles['PMOD1_Ch3']['kind'],'I')
            profiles['PMOD1_Ch3'].update(kind='dV',full_scale_volts=3.6)
            setup=Path(d)/'gui_board_inputs.json'
            setup.write_text(json.dumps({'version':1,'channel_profiles':profiles}))
            corrected=input_profiles(cfg)
            self.assertEqual(corrected['PMOD1_Ch3']['kind'],'dV')
            self.assertEqual(corrected['PMOD1_Ch3']['full_scale_volts'],3.6)
            setup.write_text('{"version":1,"channel_profiles":{}}')
            with self.assertRaises(ValueError):input_profiles(cfg)

    def test_statistics(self):
        s=summaries([np.array([1.,3.]),np.array([3.,5.])])
        self.assertEqual(s['mean'],3.)
        self.assertAlmostEqual(s['capture_std'],np.sqrt(2))
        self.assertAlmostEqual(s['sample_std'],np.std([1,3,3,5],ddof=1))
        self.assertIsNone(summaries([np.array([1.,2.])])['capture_std'])
    def test_duplicate_inputs(self):
        p=default_profile();p['mappings'][1]['channel']='A'
        with self.assertRaises(ValueError):validate_profile(p)
    def test_adc_column_identity_and_units(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'fadc.dat'
            path.write_text('# p_nr | tstmp | ADC_CH4(PMOD1_I4) | ADC_CH0(PMOD1_dV0) |\n0 0x01 4194304 4194304\n1 0x02 -4194304 2097152\n')
            profiles={'PMOD1_Ch0':dict(kind='dV',full_scale_volts=3.6,bits=24,shunt_ohms=.005),
                'PMOD1_Ch4':dict(kind='I',full_scale_volts=.012,bits=24,shunt_ohms=.005)}
            v,t=decode_file(path,['PMOD1_Ch0','PMOD1_Ch4'],profiles)
            np.testing.assert_allclose(v['PMOD1_Ch0'],[1.8,.9])
            np.testing.assert_allclose(v['PMOD1_Ch4'],[1.2,-1.2])
            np.testing.assert_array_equal(t,[1,2])
            with self.assertRaises(ValueError):decode_file(path,['PMOD2_Ch0'],profiles)
    def test_saturation(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a';p.write_text('# ADC_CH0(PMOD1_dV0)\n0 0x1 8388607\n1 0x2 0\n')
            profile={'PMOD1_Ch0':dict(kind='dV',full_scale_volts=3.6,bits=24,shunt_ohms=.005)}
            with self.assertRaisesRegex(ValueError,'saturated'):decode_file(p,['PMOD1_Ch0'],profile)
    def test_multiple_boards_simulation(self):
        p=default_profile();p['backend']='venelin'
        p['boards'].append({'id':'board2','image':'','testpoints':[{'name':'Vout','x':.5,'y':.5}]})
        for i,m in enumerate(p['mappings']):m['channel']=f'PMOD1_Ch{i}'
        p['mappings'].append(dict(board='board2',testpoint='Vout',channel='PMOD1_Ch2',probe=1,range=2))
        with tempfile.TemporaryDirectory() as d,patch('station.plot_mapped'):
            out=run_mapped(self.args(d),p)
            meta=json.loads((out/'metadata.json').read_text());self.assertEqual(meta['completed_points'],4)
            self.assertEqual(meta['status'],'complete')
            self.assertEqual(len((out/'measurements.csv').read_text().splitlines()),13)
    def test_stop_disables_supply(self):
        stop=threading.Event()
        class Supply(SimSupply):
            off=False
            def output_off(self):self.off=True
        s=Supply();p=default_profile();r=SimMapped(s,p['mappings'])
        with tempfile.TemporaryDirectory() as d,patch('station.plot_mapped'):
            with self.assertRaises(InterruptedError):run_mapped(self.args(d),p,stop,lambda _:stop.set(),s,r)
        self.assertTrue(s.off)
    def test_adc_profile_fitted_types(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'c.toml';p.write_text('[BoardInputs.PMOD1]\nKinds=["dV","dV","dV","dV","I","I","aV","aV"]\n')
            profiles=input_profiles(p);self.assertEqual(profiles['PMOD1_Ch3']['kind'],'dV')

if __name__=='__main__':unittest.main()

