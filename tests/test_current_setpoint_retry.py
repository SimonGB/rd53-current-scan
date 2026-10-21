"""HMP4040 setpoint verifier regression tests: transient/stale readback vs persistent fault."""
import unittest
from unittest.mock import patch

from instruments import HMP4040


class FakeSupply(HMP4040):
    """Avoid serial hardware while exercising the real verification routine."""
    def __init__(self, readings):
        self.channel = 2
        self.readings = iter(readings)
        self.commands = []

    def write(self, command):
        self.commands.append(command)

    def number(self, command):
        if command != 'CURR?':
            raise AssertionError('Unexpected instrument query')
        return next(self.readings)


class CurrentSetpointTests(unittest.TestCase):
    @patch('instruments.time.sleep')
    def test_immediate_setpoint_match(self, sleep):
        supply = FakeSupply([1.155])
        supply.set_current_limit(1.155)
        self.assertEqual(supply.commands, ['CURR 1.155000000'])
        sleep.assert_not_called()

    @patch('instruments.time.sleep')
    def test_one_stale_previous_step_then_correct_setting(self, sleep):
        supply = FakeSupply([1.165, 1.155])
        supply.set_current_limit(1.155)
        self.assertEqual(supply.commands, ['CURR 1.155000000'])
        self.assertEqual(sleep.call_count, 1)

    @patch('instruments.time.sleep')
    def test_single_reapply_corrects_persistent_stale_readback(self, sleep):
        supply = FakeSupply([1.165, 1.165, 1.155])
        supply.set_current_limit(1.155)
        self.assertEqual(supply.commands, ['CURR 1.155000000'] * 2)

    @patch('instruments.time.sleep')
    def test_persistent_mismatch_must_abort(self, sleep):
        supply = FakeSupply([1.165, 1.165, 1.165, 1.165])
        with self.assertRaisesRegex(RuntimeError, 'Current setpoint mismatch') as error:
            supply.set_current_limit(1.155)
        self.assertIn('output channel 2', str(error.exception))
        self.assertIn('readbacks', str(error.exception))
        self.assertEqual(supply.commands, ['CURR 1.155000000'] * 2)

    @patch('instruments.time.sleep')
    def test_small_readback_difference_allowed_by_original_tolerance(self, sleep):
        supply = FakeSupply([1.1558])
        supply.set_current_limit(1.155)
        self.assertEqual(supply.commands, ['CURR 1.155000000'])

    @patch('instruments.time.sleep')
    def test_input_validation(self, sleep):
        for value in (0, -0.1, float('nan'), float('inf')):
            with self.subTest(value=value):
                supply = FakeSupply([])
                with self.assertRaises(ValueError):
                    supply.set_current_limit(value)
                self.assertEqual(supply.commands, [])


if __name__ == '__main__':
    unittest.main()
