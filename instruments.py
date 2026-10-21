"""HMP4040 scan adapter; based on the earlier charge-pump driver."""
import math
import time
from hmp4040_base import HMP4040 as BaseHMP4040


class HMP4040(BaseHMP4040):
    def connect(self):
        import serial
        self.ser = serial.Serial(self.port, baudrate=self.baudrate,
                                 timeout=self.timeout, write_timeout=self.timeout)
        time.sleep(0.2)
        self.ser.reset_input_buffer()
        self.identity = self.query('*IDN?')
        if 'HMP4040' not in self.identity.upper():
            raise RuntimeError(f'Expected HMP4040, received {self.identity!r}')
        self.select_channel(self.channel)
        self.master_was_on = None
        # No *RST: it would reset other channels and their limits.

    def write(self, command):
        super().write(command)
        self.ser.flush()
        time.sleep(self.command_delay)

    def number(self, command):
        value = float(self.query(command))
        if not math.isfinite(value):
            raise RuntimeError(f'Non-finite response to {command}')
        return value

    def set_current_limit(self, current):
        """Program the current limit and verify it without accepting stale reads.

        A busy HMP4040 can occasionally return the preceding 10 mA sweep
        setting just after a CURR command. The strict 0.1% tolerance stays
        unchanged: only wait/re-query and, if needed, reapply the requested
        setting once. Never continue at a persistently wrong limit.
        """
        if not math.isfinite(current) or current <= 0:
            raise ValueError('Current must be positive and finite')
        target = f'CURR {current:.9f}'
        tolerance = max(0.00001, current * 0.001)
        self.write(target)
        observations = []
        # Poll a few times before retransmitting: a readback may lag the write.
        for attempt in range(2):
            actual = self.number('CURR?')
            observations.append(actual)
            if abs(actual - current) <= tolerance:
                if len(observations) > 1:
                    print(f'HMP4040 current setpoint settled: requested {current:.6f} A; '
                          f'readbacks {observations} A', flush=True)
                return
            time.sleep(0.12)
        # Re-assert only the *same* requested limit, then verify twice more.
        self.write(target)
        for attempt in range(2):
            actual = self.number('CURR?')
            observations.append(actual)
            if abs(actual - current) <= tolerance:
                print(f'HMP4040 current setpoint recovered after retry: '
                      f'requested {current:.6f} A; readbacks {observations} A',
                      flush=True)
                return
            if attempt == 0:
                time.sleep(0.12)
        raise RuntimeError(
            f'Current setpoint mismatch: requested {current:.6f} A, '
            f'last readback {observations[-1]:.6f} A '
            f'(tolerance {tolerance:.6f} A; readbacks {observations}; '
            f'output channel {self.channel}). Aborting scan.'
        )

    def prepare_scan(self, voltage, current):
        self.master_was_on = bool(int(self.query('OUTP:GEN?')))
        # Enabling the master can activate other selected channels. Reject that
        # situation rather than change their selection or turn them on.
        if not self.master_was_on:
            for channel in range(1, 5):
                if channel == self.channel:
                    continue
                self.write(f'INST OUT{channel}')
                if int(self.query('OUTP:SEL?')):
                    self.select_channel(self.channel)
                    raise RuntimeError(f'CH{channel} is selected with master OFF; deselect it before scanning')
        self.select_channel(self.channel)
        self.write('OUTP:SEL OFF')
        self.set_current_limit(current)
        self.set_voltage(voltage)
        time.sleep(self.voltage_settling_time)
        self.write('OUTP:SEL ON')
        self.write('OUTP:GEN ON')
        time.sleep(self.output_on_verify_delay)
        self._verify_voltage_setting(voltage)
        if not int(self.query('OUTP:SEL?')) or not int(self.query('OUTP:GEN?')):
            raise RuntimeError('Supply output failed to enable')

    def output_off(self):
        self.select_channel(self.channel)
        self.write('OUTP:SEL OFF')
        if self.master_was_on is False:
            self.write('OUTP:GEN OFF')

    def readback(self):
        return self.number('MEAS:CURR?'), self.number('MEAS:VOLT?')
