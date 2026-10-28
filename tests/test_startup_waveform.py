import json
import tempfile
import threading
import unittest
from pathlib import Path
import numpy as np
from mapping import default_profile
from startup_waveform import run, parse_currents, validate

class StartupWaveformTests(unittest.TestCase):
    def settings(self,d):
        return dict(currents_ma=[20.,50.],trigger_channel='A',trigger_v=.1,pre_ms=1.,post_ms=4.,sample_rate=1000000.,timeout_s=1.,off_s=.001,max_current_ma=100.,voltage_limit=2.,repeats=2,channel=2,port='fake',output=Path(d),name='test',simulate=True)
    def test_simulated_waveforms_and_overlay(self):
        with tempfile.TemporaryDirectory() as d:
            out=run(self.settings(d),default_profile());meta=json.loads((out/'metadata.json').read_text())
            self.assertEqual(meta['status'],'complete');self.assertEqual(len(meta['captures']),4)
            with np.load(out/meta['captures'][0]['file']) as f:
                self.assertAlmostEqual(f['time_s'][0],-.001);self.assertAlmostEqual(f['time_s'][1000],0);self.assertEqual(len(f['A']),5000)
            self.assertTrue((out/'startup_overlay.pdf').exists())
    def test_voltage_current_grid_and_verified_ceiling(self):
        with tempfile.TemporaryDirectory() as d:
            settings=self.settings(d);settings.update(voltage_limits_v=[1.8,2.0],max_voltage_v=2.0,repeats=1)
            out=run(settings,default_profile());meta=json.loads((out/'metadata.json').read_text())
            pairs=[(x['voltage_limit_v'],x['current_ma']) for x in meta['captures']]
            self.assertEqual(pairs,[(1.8,20.),(1.8,50.),(2.,20.),(2.,50.)])
            self.assertEqual(len(list(out.glob('*.npz'))),4)
            settings['voltage_limits_v']=[1.8,2.1]
            with self.assertRaisesRegex(ValueError,'voltage ceiling'):validate(settings,default_profile())

    def test_timeout_shuts_down_and_saves_partial_status(self):
        class Supply:
            def __init__(self):self.calls=[]
            def connect(self):self.calls.append('connect')
            def prepare(self,*a):self.calls.append('prepare')
            def on(self):self.calls.append('on')
            def off(self):self.calls.append('off')
            def close(self):self.calls.append('close')
        class Scope:
            def connect(self):pass
            def capture(self,action,cancel):action();raise TimeoutError('no trigger')
            def close(self):pass
        with tempfile.TemporaryDirectory() as d:
            supply=Supply()
            with self.assertRaisesRegex(RuntimeError,'no trigger'):run(self.settings(d),default_profile(),supply=supply,scope=Scope())
            self.assertIn('off',supply.calls);self.assertLess(supply.calls.index('on'),supply.calls.index('off'))
            meta=json.loads(next(Path(d).rglob('metadata.json')).read_text());self.assertEqual(meta['status'],'failed')
    def test_limits_checked(self):
        with tempfile.TemporaryDirectory() as d:
            s=self.settings(d);s['currents_ma']=[101]
            with self.assertRaises(ValueError):validate(s,default_profile())
            with self.assertRaises(ValueError):parse_currents('NaN')

class HardwareSequenceTests(unittest.TestCase):
    def test_scope_arms_before_action_and_returns_four_channels(self):
        import sys, types
        from unittest.mock import patch
        from ctypes import c_int16, c_int32, cast, POINTER
        from startup_waveform import TriggeredPico
        calls=[]
        class SDK:
            def ps6000aSetDataBuffer(self,*args):calls.append('buffer');return 0
            def ps6000aSetSimpleTrigger(self,handle,enabled,*args):calls.append('trigger_on' if enabled else 'trigger_off');return 0
            def ps6000aRunBlock(self,handle,pre,post,*args):calls.append(('run',pre,post));return 0
            def ps6000aGetScopeState(self,handle,state):calls.append('state');cast(state,POINTER(c_int32))[0]=2;return 0
            def ps6000aIsReady(self,handle,ready):calls.append('ready');cast(ready,POINTER(c_int16))[0]=1;return 0
            def ps6000aGetValues(self,*args):calls.append('values');return 0
            def ps6000aStop(self,*args):calls.append('stop');return 0
        fake_sdk=SDK();mod=types.ModuleType('picosdk.ps6000a');mod.ps6000a=fake_sdk;parent=types.ModuleType('picosdk');parent.ps6000a=mod
        class Scope:
            sample_interval=.000001;handle=c_int16(1);data_type=0;ratio_mode=0;clear_all=1;add=2;timebase=1;adc_max=32767;resolution_bits=10
            channel_settings={ch:dict(channel=i,range=2.,probe=1.) for i,ch in enumerate('ABCD')}
            def _validate_sample_count(self,n):pass
            def check(self,status,name):assert status==0
        instance=TriggeredPico.__new__(TriggeredPico);instance.scope=Scope();instance.settings=dict(pre_ms=1,post_ms=4,trigger_channel='A',trigger_v=.1,timeout_s=1)
        with patch.dict(sys.modules,{'picosdk':parent,'picosdk.ps6000a':mod}):
            t,values,details=instance.capture(lambda:calls.append('power_on'),threading.Event())
        self.assertLess(calls.index('state'),calls.index('power_on'))
        self.assertIn(('run',1000,4000),calls);self.assertEqual(set(values),set('ABCD'));self.assertAlmostEqual(t[1000],0)
        self.assertEqual(calls[-2:],['stop','trigger_off'])
    def test_unexpected_trigger_prevents_power_on(self):
        import sys,types
        from unittest.mock import patch
        from ctypes import c_int16,c_int32,cast,POINTER
        from startup_waveform import TriggeredPico
        calls=[]
        class SDK:
            def ps6000aSetDataBuffer(self,*a):return 0
            def ps6000aSetSimpleTrigger(self,*a):return 0
            def ps6000aRunBlock(self,*a):return 0
            def ps6000aGetScopeState(self,h,p):cast(p,POINTER(c_int32))[0]=3;return 0
            def ps6000aStop(self,*a):calls.append('stop');return 0
        mod=types.ModuleType('picosdk.ps6000a');mod.ps6000a=SDK();parent=types.ModuleType('picosdk');parent.ps6000a=mod
        class Scope:
            sample_interval=.000001;handle=c_int16(1);data_type=0;ratio_mode=0;clear_all=1;add=2;timebase=1;adc_max=32767
            channel_settings={'A':dict(channel=0,range=2.,probe=1.)}
            def _validate_sample_count(self,n):pass
            def check(self,status,name):assert status==0
        instance=TriggeredPico.__new__(TriggeredPico);instance.scope=Scope();instance.settings=dict(pre_ms=1,post_ms=4,trigger_channel='A',trigger_v=.1,timeout_s=1)
        with patch.dict(sys.modules,{'picosdk':parent,'picosdk.ps6000a':mod}):
            with self.assertRaisesRegex(RuntimeError,'Unexpected trigger'):instance.capture(lambda:calls.append('power_on'),threading.Event())
        self.assertNotIn('power_on',calls);self.assertIn('stop',calls)
