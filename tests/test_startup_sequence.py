"""Regression checks for guarded Shunt-LDO current-step startup characterization."""
import argparse
import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import numpy as np
from mapping import default_profile
from startup_sequence_test import Step, staircase, sequences, validate_args, perform, build_parser


def args_for(root, simulate=True, armed=False):
    p=build_parser()
    command=['--voltage-limit','2.5','--output',str(root),
             '--max-current-ma','100','--repeats','2',
             '--step-ma','10','--target-ma','40',
             '--direct-ma','25','--dwell','0','--off-time','0']
    command.append('--simulate' if simulate else '--arm')
    args=p.parse_args(command)
    if armed:
        args.profile=Path('fixture_profile.json')
    return args


class FakeSupply:
    identity='Fake HMP'
    def __init__(self):
        self.current=0
        self.enabled=False
        self.actions=[]
    def connect(self):self.actions.append('connect')
    def prepare_scan(self,voltage,current):
        self.current=current
        self.enabled=True
        self.actions.append(('on',current))
    def set_current_limit(self,current):
        self.current=current
        self.actions.append(('set',current))
    def output_off(self):
        self.enabled=False
        self.actions.append('off')
    def readback(self):
        return self.current, 1.8 if self.enabled else 0
    def close(self):self.actions.append('close')


class FakeReader:
    identity='Fake Pico'
    def __init__(self,supply,fail=False):
        self.supply=supply;self.fail=fail
    def connect(self):pass
    def capture(self):
        if self.fail:
            raise RuntimeError('Injected Pico failure')
        current=self.supply.current
        # Simulate a regulator starting only for the direct 25 mA jump.
        voltage=1.8 if abs(current-.025)<1e-9 else .2
        return dict(A=np.full(12,voltage/2),B=np.full(12,voltage))
    def close(self):pass


class StartupTests(unittest.TestCase):
    def test_staircase_and_direct_profiles(self):
        self.assertEqual([v.current_ma for v in staircase(40,10,0)],[10,20,30,40])
        self.assertEqual([x for x,_ in sequences('both',25,40,10,0)],['staircase','direct'])
        with self.assertRaises(ValueError):
            staircase(10,20,1)

    def test_disarmed_hardware_refused_before_acquisition(self):
        with tempfile.TemporaryDirectory() as folder:
            args=args_for(folder,simulate=False,armed=False)
            with self.assertRaisesRegex(ValueError,'Hardware is disabled'):
                validate_args(args,default_profile())
            args.arm=True
            with self.assertRaisesRegex(ValueError,'wiring'):
                validate_args(args,default_profile())

    def test_simulated_both_sequences_off_between_repeats(self):
        with tempfile.TemporaryDirectory() as folder:
            args=args_for(folder)
            supply=FakeSupply()
            reader=FakeReader(supply)
            out=perform(args,default_profile(),supply=supply,reader=reader,sleep=lambda _:None)
            self.assertTrue((out/'metadata.json').exists())
            self.assertTrue((out/'startup_steps.csv').exists())
            self.assertTrue((out/'startup_comparison.png').exists())
            with (out/'startup_steps.csv').open() as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(len(rows), 2*(4+1))
            self.assertEqual(len([a for a in supply.actions if a=='off']),5)
            self.assertTrue(all(float(row['measured_ma'])<=100 for row in rows))
            direct=[r for r in rows if r['sequence']=='direct']
            self.assertEqual([r['started'] for r in direct],['True','True'])
            self.assertFalse(supply.enabled)

    def test_supply_shutdown_on_capture_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            args=args_for(folder)
            supply=FakeSupply()
            with self.assertRaisesRegex(RuntimeError,'Injected Pico failure'):
                perform(args,default_profile(),supply=supply,
                        reader=FakeReader(supply,fail=True),sleep=lambda _:None)
            self.assertFalse(supply.enabled)
            self.assertIn('off',supply.actions)
            self.assertIn('close',supply.actions)

    def test_reject_unsafe_currents_and_compliance(self):
        with tempfile.TemporaryDirectory() as folder:
            args=args_for(folder)
            args.direct_ma=120
            with self.assertRaises(ValueError):
                validate_args(args,default_profile())
            args.direct_ma=25
            args.voltage_limit=32
            with self.assertRaises(ValueError):
                validate_args(args,default_profile())


if __name__=='__main__':
    unittest.main()
