import csv
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from mapping import default_profile
from run_comments import RunComments
from scan import parser,SimSupply
from station import run_mapped,SimMapped
from venelin import Venelin

class LiveTests(unittest.TestCase):
    def args(self,d):
        return parser().parse_args(['--simulate','--start','.01','--stop','.02','--step','.01',
          '--voltage-limit','3.5','--max-current','.05','--output',d,'--comments','Initial note'])
    def test_live_notes_revision_and_failed_run(self):
        notes=RunComments('Initial note');stop=threading.Event()
        def point(batch):
            notes.update('Changed during scan\nNew cable');stop.set()
        with tempfile.TemporaryDirectory() as d,patch('station.plot_mapped'):
            with self.assertRaises(InterruptedError):run_mapped(self.args(d),default_profile(),stop,point,notes=notes)
            out=next((Path(d)/'rd53_v2').iterdir())
            self.assertEqual((out/'comments.txt').read_text(),'Changed during scan\nNew cable')
            meta=json.loads((out/'metadata.json').read_text())
            self.assertEqual(meta['comments_revision'],1)
            history=json.loads((out/'comments_history.json').read_text())
            self.assertEqual(history[1]['last_completed_point'],0)
            with (out/'measurements.csv').open() as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(rows[0]['comments'],'Initial note')
            notes.update('Final note')
            self.assertEqual(json.loads((out/'metadata.json').read_text())['comments'],'Final note')
    def test_preflight_failure_never_connects_supply(self):
        class Supply(SimSupply):
            connected=False
            def connect(self):self.connected=True
        class Reader(SimMapped):
            def preflight(self):raise TimeoutError('ADC timeout')
        s=Supply();p=default_profile();r=Reader(s,p['mappings'])
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(TimeoutError):run_mapped(self.args(d),p,supply=s,reader=r)
        self.assertFalse(s.connected)
    def test_pmod_two_initialization_mask(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=Path(d)/'config.toml';cfg.write_text('[Calibration]\nPrecisionBits=24\n')
            r=Venelin(d,cfg,Path(d)/'fadc.dat',['PMOD2_Ch1','PMOD2_Ch3'],1000,.1,threading.Event())
            calls=[];r.command=lambda *args:calls.append(args);r.connect()
            args=calls[-1][1]
            self.assertEqual(args[args.index('--chip-mask')+1],'0x2')
            self.assertEqual(args[args.index('--chip-select')+1],'1')
