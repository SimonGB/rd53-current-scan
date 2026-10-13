"""
Driver for the Rohde & Schwarz / HAMEG HMP4040 power supply.

Communication:
    Serial SCPI

Tested settings:
    Linux
    /dev/ttyUSB0
"""

import time
import serial


class HMP4040:
    def __init__(
        self,
        port="/dev/ttyUSB0",
        baudrate=9600,
        timeout=1.0,
        channel=1,
        command_delay=0.05,
        voltage_settling_time=0.25,
        output_on_verify_delay=0.02,
    ):

        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.channel = channel
        self.command_delay = float(command_delay)
        self.voltage_settling_time = float(voltage_settling_time)
        self.output_on_verify_delay = float(output_on_verify_delay)
        self.requested_voltage = None
        self.output_prepared = False

        self.ser = None

    #######################################################################
    # Connection
    #######################################################################

    def connect(self):

        self.ser = serial.Serial(
            self.port,
            baudrate=self.baudrate,
            timeout=self.timeout,
        )

        time.sleep(0.2)

        self.write("*CLS")
        self.write("*RST")

        print("Connected to:")
        print(self.query("*IDN?"))

        self.select_channel(self.channel)

    def close(self):

        if self.ser is not None:
            self.ser.close()

    #######################################################################
    # Low-level communication
    #######################################################################

    def write(self, command):

        if self.ser is None or not self.ser.is_open:
            raise RuntimeError("HMP4040 is not connected")

        self.ser.write((command + "\n").encode())

    def query(self, command):

        self.write(command)
        response = self.ser.readline().decode().strip()

        if not response:
            raise RuntimeError(
                f"HMP4040 did not respond to {command!r}"
            )

        return response

    #######################################################################
    # Channel selection
    #######################################################################

    def select_channel(self, channel):

        channel = int(channel)

        if channel not in (1, 2, 3, 4):
            raise ValueError("HMP4040 channel must be 1, 2, 3, or 4")

        self.channel = channel
        self.write(f"INST OUT{channel}")

    #######################################################################
    # Output
    #######################################################################

    def prepare_output(self, voltage=None):
        """Prepare a channel while the global output remains disabled.

        This deliberately performs the slow channel-selection and voltage-
        settling sequence before an oscilloscope capture is started.  The
        later output_on() call therefore contains only the electrical master-
        output edge and a readback check.
        """

        if voltage is not None:
            self.requested_voltage = float(voltage)
        if self.requested_voltage is None:
            self.requested_voltage = self.get_voltage_setting()

        self.select_channel(self.channel)
        time.sleep(self.command_delay)
        self.write("OUTP:SEL ON")
        time.sleep(self.command_delay)
        self.write(f"VOLT {self.requested_voltage:.6f}")
        time.sleep(self.voltage_settling_time)
        self._verify_voltage_setting(self.requested_voltage, reapply=True)
        self.output_prepared = True

    def output_on(self):
        # Do not place the slow preparation sequence inside a PicoScope block.
        # Callers normally prepare explicitly; this fallback keeps direct use
        # of the driver safe.
        if not self.output_prepared:
            self.prepare_output()

        expected_voltage = self.requested_voltage

        self.write("OUTP:GEN ON")
        time.sleep(self.output_on_verify_delay)

        # Never continue a measurement silently at the wrong voltage.  If the
        # instrument changes the setpoint while enabling the master output,
        # disable it immediately and report the readback.
        actual_voltage = self.get_voltage_setting()
        if abs(actual_voltage - expected_voltage) > 0.002:
            self.write("OUTP:GEN OFF")
            raise RuntimeError(
                "HMP4040 changed its voltage setting while enabling the "
                f"output: requested {expected_voltage:.6g} V, read back "
                f"{actual_voltage:.6g} V. Output was disabled."
            )

    def output_off(self):
        self.write("OUTP:GEN OFF")

    #######################################################################
    # Voltage
    #######################################################################

    def set_voltage(self, voltage):

        voltage = float(voltage)

        if voltage < 0:
            raise ValueError("Supply voltage must be non-negative")

        self.select_channel(self.channel)
        self.write(f"VOLT {voltage:.6f}")
        self.requested_voltage = voltage
        self.output_prepared = False
        time.sleep(self.command_delay)
        self._verify_voltage_setting(voltage, reapply=True)

    def _verify_voltage_setting(self, expected_voltage, reapply=False):
        """Check the selected-channel voltage against the requested value."""

        expected_voltage = float(expected_voltage)
        actual_voltage = self.get_voltage_setting()
        if abs(actual_voltage - expected_voltage) <= 0.002:
            return actual_voltage

        if reapply:
            self.write(f"VOLT {expected_voltage:.6f}")
            time.sleep(0.02)
            actual_voltage = self.get_voltage_setting()
            if abs(actual_voltage - expected_voltage) <= 0.002:
                return actual_voltage

        raise RuntimeError(
            "HMP4040 voltage-setting verification failed: requested "
            f"{expected_voltage:.6g} V, read back {actual_voltage:.6g} V."
        )

    def get_voltage_setting(self):

        return float(self.query("VOLT?"))

    def measure_voltage(self):

        return float(self.query("MEAS:VOLT?"))

    #######################################################################
    # Current
    #######################################################################

    def set_current_limit(self, current):

        current = float(current)

        if current <= 0:
            raise ValueError("Current limit must be greater than zero")

        self.write(f"CURR {current:.6f}")

    def get_current_limit(self):

        return float(self.query("CURR?"))

    def measure_current(self):

        return float(self.query("MEAS:CURR?"))

    #######################################################################
    # Convenience
    #######################################################################

    def ramp_voltage(
        self,
        target,
        step=0.05,
        delay=0.02,
    ):

        current = self.get_voltage_setting()

        if target > current:

            while current < target:

                current += step

                if current > target:
                    current = target

                self.set_voltage(current)
                time.sleep(delay)

        else:

            while current > target:

                current -= step

                if current < target:
                    current = target

                self.set_voltage(current)
                time.sleep(delay)

    #######################################################################

    def beep(self):

        self.write("SYST:BEEP")
