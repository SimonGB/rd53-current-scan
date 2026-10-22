"""Tests for Rigol DG4162 frequency-sweep commands and output safety gates."""
import tempfile
from pathlib import Path
import unittest

from dg4162_frequency_sweep import make_parser,run,frequency_points


class FakeDG:
    def __init__(self, fail_frequency=False):
        self.commands=[]
        self.frequency=0
        self.fail_frequency=fail_frequency
        self.timeout=None
        self.write_termination=None
        self.read_termination=None
        self.closed=False
    def write(self,cmd):
        self.commands.append(cmd)
        if cmd.startswith(':SOUR1:FREQ '):
            self.frequency=float(cmd.split()[-1])
    def query(self,cmd):
        if cmd=='*IDN?':
            return 'RIGOL TECHNOLOGIES,DG4162,DG416200000,00.01'
        if cmd==':SOUR1:FREQ?':
            return str(self.frequency+50 if self.fail_frequency else self.frequency)
        raise AssertionError(cmd)
    def close(self):
        self.closed=True


class FakeManager:
    def __init__(self,device):
        self.device=device
    def open_resource(self,name):
        return self.device
    def list_resources(self):
        return ('USB0::DG4162::INSTR',)


def make_args(*options):
    return make_parser().parse_args(['--resource','USB0::DG4162::INSTR',*options])


class DG4162Tests(unittest.TestCase):
    def test_frequency_points(self):
        self.assertEqual(frequency_points(1,100,3,'log'),[1,10,100])
        self.assertEqual(frequency_points(10,30,3,'linear'),[10,20,30])
        with self.assertRaises(ValueError):
            frequency_points(0,100,3,'log')

    def test_safe_preview_does_not_change_instrument(self):
        dev=FakeDG()
        result=run(make_args('--start-hz','1','--stop-hz','100','--points','3'),
                   manager=FakeManager(dev))
        self.assertEqual(len(result['preview']),3)
        self.assertEqual(dev.commands,[])
        self.assertTrue(dev.closed)

    def test_output_gating_requires_topology_ack_and_amplitude(self):
        dev=FakeDG()
        with self.assertRaisesRegex(ValueError,'confirm-safe-interface'):
            run(make_args('--enable-output'),manager=FakeManager(dev))
        with self.assertRaisesRegex(ValueError,'amplitude-vpp'):
            run(make_args('--enable-output','--confirm-safe-interface'),
                manager=FakeManager(dev))
        self.assertFalse(dev.commands)

    def test_armed_sweep_readbacks_csv_and_disables(self):
        dev=FakeDG()
        with tempfile.TemporaryDirectory() as temp:
            args=make_args('--enable-output','--confirm-safe-interface',
                '--amplitude-vpp','0.2','--offset-v','0','--points','3',
                '--start-hz','10','--stop-hz','100','--min-dwell-s','.1',
                '--max-dwell-s','.2','--output',temp)
            result=run(args,manager=FakeManager(dev),sleeper=lambda seconds:None)
            self.assertEqual(len((result/'frequency_steps.csv').read_text().splitlines()),4)
            self.assertIn(':OUTP1 ON',dev.commands)
            self.assertEqual(dev.commands[-1],':OUTP1 OFF')
            self.assertTrue(dev.closed)
            self.assertEqual(dev.write_termination,'\n')

    def test_mismatch_still_shuts_down(self):
        dev=FakeDG(fail_frequency=True)
        with tempfile.TemporaryDirectory() as temp:
            args=make_args('--enable-output','--confirm-safe-interface',
                '--amplitude-vpp','0.2','--start-hz','10','--stop-hz','100',
                '--points','3','--output',temp)
            with self.assertRaisesRegex(RuntimeError,'readback mismatch'):
                run(args,manager=FakeManager(dev),sleeper=lambda seconds:None)
            self.assertEqual(dev.commands[-1],':OUTP1 OFF')
            self.assertTrue(dev.closed)

    def test_list_resources_returns_resource_names_not_frequency_pairs(self):
        from dg4162_frequency_sweep import main
        from unittest.mock import patch
        import io
        dev=FakeDG()
        args=make_args('--list-resources')
        result=run(args,manager=FakeManager(dev))
        self.assertEqual(result,['USB0::DG4162::INSTR'])
        with patch('sys.argv',['dg4162_frequency_sweep.py','--list-resources']), \
             patch('dg4162_frequency_sweep.run',return_value=result), \
             patch('sys.stdout',new_callable=io.StringIO) as stdout:
            main()
        self.assertIn('USB0::DG4162::INSTR',stdout.getvalue())

    def test_cancel_during_dwell_disables_generator(self):
        dev=FakeDG()
        class InterruptingEvent:
            def is_set(self): return False
            def wait(self, seconds): return True
        event=InterruptingEvent()
        with tempfile.TemporaryDirectory() as temp:
            args=make_args('--enable-output','--confirm-safe-interface',
                '--amplitude-vpp','0.2','--start-hz','10','--stop-hz','100',
                '--points','3','--output',temp)
            with self.assertRaisesRegex(InterruptedError,'stopped by user'):
                run(args,manager=FakeManager(dev),stop_event=event)
            self.assertEqual(dev.commands[-1],':OUTP1 OFF')
            self.assertTrue(dev.closed)

    def test_direct_linux_usbtmc_identification_without_visa(self):
        from unittest.mock import patch
        from dg4162_frequency_sweep import DG4162
        import os
        commands=[]
        descriptor=73
        def fake_write(fd, payload):
            self.assertEqual(fd,descriptor)
            commands.append(payload)
            return len(payload)
        with patch('dg4162_frequency_sweep.os.open',return_value=descriptor) as opened, \
             patch('dg4162_frequency_sweep.os.write',side_effect=fake_write), \
             patch('dg4162_frequency_sweep.os.read',
                   return_value=b'RIGOL TECHNOLOGIES,DG4162,123456,00.01\n'), \
             patch('dg4162_frequency_sweep.os.close') as closed:
            device=DG4162('/dev/usbtmc0')
            identity=device.connect()
            self.assertIn('DG4162',identity)
            self.assertEqual(commands,[b'*IDN?\n'])
            device.close()
            opened.assert_called_once()
            closed.assert_called_once_with(descriptor)

    def test_direct_usbtmc_read_timeout_gives_actionable_error(self):
        from unittest.mock import patch
        from dg4162_frequency_sweep import DG4162
        with patch('dg4162_frequency_sweep.os.open',return_value=73), \
             patch('dg4162_frequency_sweep.os.write',return_value=6), \
             patch('dg4162_frequency_sweep.os.read',side_effect=TimeoutError(110,'Connection timed out')), \
             patch('dg4162_frequency_sweep.os.close'):
            device=DG4162('/dev/usbtmc0')
            try:
                with self.assertRaisesRegex(TimeoutError,'USB Dev = PC'):
                    device.connect()
            finally:
                device.close()

    def test_kernel_resource_list_includes_usbtmc0(self):
        from unittest.mock import patch
        from pathlib import Path
        from dg4162_frequency_sweep import discover_resources
        with patch('dg4162_frequency_sweep.Path.glob',
                   return_value=[Path('/dev/usbtmc0')]):
            resources=discover_resources(manager=FakeManager(FakeDG()))
        self.assertIn('/dev/usbtmc0',resources)


if __name__=='__main__':
    unittest.main()
