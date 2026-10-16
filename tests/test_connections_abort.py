import csv
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import numpy as np
from connections import add_connection, connection_points
from mapping import default_profile, validate_profile
from board_setup import active_profile, delete_testpoint
from run_comments import RunComments
from scan import parser, SimSupply
from station import run_mapped, SimMapped


class ConnectionTests(unittest.TestCase):
    def profile(self):
        p=default_profile();p['backend']='venelin'
        for i,m in enumerate(p['mappings'],1):m['channel']=f'PMOD1_Ch{i}'
        p['boards'].append(dict(id='board2',image='',testpoints=[dict(name='Vout',x=.5,y=.5)]))
        return add_connection(p,dict(board='board2',point='GND'),dict(board='board1',point='GND'),'PMOD1_Ch3')

    def test_profile_endpoints_and_removal(self):
        p=self.profile();validate_profile(p)
        with self.assertRaises(ValueError):
            add_connection(p,dict(board='board1',point='GND'),dict(board='board1',point='GND'),'PMOD1_Ch4')
        p=add_connection(p,dict(board='board2',point='Vout'),dict(board=None,point='GND'),'PMOD1_Ch4')
        p=delete_testpoint(p,'board2','Vout')
        self.assertEqual(len(connection_points(p)),1)
        p['boards'][1]['enabled']=False
        self.assertFalse(connection_points(active_profile(p)))

    def test_direct_signed_measurement_and_export(self):
        p=self.profile();s=SimSupply()
        class Reader(SimMapped):
            def capture(self):return {m['channel']:np.array([-.012,-.008]) for m in self.mappings}
        with tempfile.TemporaryDirectory() as d,patch('station.plot_mapped'):
            args=parser().parse_args(["--voltage-limit","3.5","--max-current",".45",'--simulate','--start','.01','--stop','.02','--step','.01','--output',d])
            out=run_mapped(args,p,supply=s,reader=Reader(s,p['mappings']))
            with (out/'measurements.csv').open() as stream:rows=list(csv.DictReader(stream))
            row=next(r for r in rows if r['board']=='Connections')
            self.assertAlmostEqual(float(row['mean']),-.01)
            self.assertEqual(row['positive_endpoint'],'board2 / GND')
            self.assertEqual(row['negative_endpoint'],'board1 / GND')
            self.assertEqual(row['unit'],'V')

    def test_wrong_input_rejected_before_supply_connect(self):
        p=self.profile();s=SimSupply();s.connected=False
        s.connect=lambda:setattr(s,'connected',True)
        r=SimMapped(s,p['mappings']);r.profiles={m['channel']:dict(kind='I') for m in p['mappings']}
        with tempfile.TemporaryDirectory() as d:
            args=parser().parse_args(["--voltage-limit","3.5","--max-current",".45",'--start','.01','--stop','.02','--step','.01','--output',d])
            with self.assertRaisesRegex(ValueError,'dV input'):run_mapped(args,p,supply=s,reader=r)
        self.assertFalse(s.connected)

    def test_abort_reason_saved_after_shutdown(self):
        notes=RunComments('keep my comments');stop=threading.Event()
        def cancel(_):
            stop.set();notes.request_abort()
        with tempfile.TemporaryDirectory() as d,patch('station.plot_mapped'):
            args=parser().parse_args(["--voltage-limit","3.5","--max-current",".45",'--simulate','--start','.01','--stop','.02','--step','.01','--output',d])
            with self.assertRaises(InterruptedError):run_mapped(args,default_profile(),stop,cancel,notes=notes)
            out=next((Path(d)/args.name).iterdir())
            self.assertTrue(json.loads((out/'abort.json').read_text())['reason_pending'])
            with self.assertRaises(ValueError):notes.abort_reason('  ')
            notes.abort_reason('Output unstable')
            meta=json.loads((out/'metadata.json').read_text())
            self.assertEqual(meta['status'],'interrupted')
            self.assertEqual(meta['abort']['reason'],'Output unstable')
            self.assertFalse(meta['abort']['reason_pending'])
            self.assertEqual(meta['comments'],'keep my comments')

    def test_abort_before_first_point_is_recorded(self):
        notes=RunComments();notes.request_abort();stop=threading.Event();stop.set()
        with tempfile.TemporaryDirectory() as d:
            args=parser().parse_args(['--voltage-limit','3.5','--max-current','.45','--simulate','--start','.01','--stop','.02','--step','.01','--output',d])
            with self.assertRaises(InterruptedError):run_mapped(args,default_profile(),stop,notes=notes)
            notes.abort_reason('Wrong run settings')
            out=next((Path(d)/args.name).iterdir())
            meta=json.loads((out/'metadata.json').read_text())
            self.assertEqual(meta['completed_points'],0)
            self.assertEqual(meta['abort']['reason'],'Wrong run settings')
