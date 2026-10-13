"""
PicoScope 6426E block-mode driver using the PS6000A API.

Channel A measures the HMP4040 input voltage and Channel B measures the
negative charge-pump output by default. Both channels use DC coupling and
1 MOhm input impedance.
"""

import time
from ctypes import (
    byref,
    c_double,
    c_int16,
    c_uint32,
    c_uint64,
    c_void_p,
)

import numpy as np
from picosdk.PicoDeviceEnums import picoEnum
from picosdk.constants import PICO_STATUS
from picosdk.ps6000a import ps6000a


# PICO_CONNECT_PROBE_RANGE values for an x1 BNC input.
VOLTAGE_RANGE_ENUM = {
    0.01: 0,
    0.02: 1,
    0.05: 2,
    0.1: 3,
    0.2: 4,
    0.5: 5,
    1.0: 6,
    2.0: 7,
    5.0: 8,
    10.0: 9,
    20.0: 10,
}


def _range_to_enum(voltage_range):
    """Return the PS6000A range enum for a full-scale voltage in volts."""

    voltage_range = float(voltage_range)

    for supported_range, enum_value in VOLTAGE_RANGE_ENUM.items():
        if np.isclose(voltage_range, supported_range):
            return enum_value

    supported = ", ".join(str(value) for value in VOLTAGE_RANGE_ENUM)
    raise ValueError(
        f"Unsupported PicoScope voltage range {voltage_range:g} V. "
        f"Choose one of: {supported} V"
    )


def _channel_to_enum(channel):
    """Return the Pico channel enum for a channel name A through H."""

    channel = str(channel).strip().upper()

    if channel not in "ABCDEFGH" or len(channel) != 1:
        raise ValueError("PicoScope channel must be one letter from A to H")

    return picoEnum.PICO_CHANNEL[f"PICO_CHANNEL_{channel}"]


class PicoScope:
    def __init__(
        self,
        input_channel="A",
        output_channel="B",
        input_range=10.0,
        output_range=10.0,
        input_probe=1.0,
        output_probe=1.0,
        resolution_bits=12,
        allow_resolution_fallback=True,
        bandwidth_limit="20MHz",
        sample_rate=10_000_000,
        capture_time=0.02,
    ):
        self.handle = c_int16()
        self.connected = False

        self.input_channel_name = str(input_channel).strip().upper()
        self.output_channel_name = str(output_channel).strip().upper()
        self.input_channel = _channel_to_enum(self.input_channel_name)
        self.output_channel = _channel_to_enum(self.output_channel_name)

        if self.input_channel == self.output_channel:
            raise ValueError("PicoScope input and output channels must differ")

        self.input_range = float(input_range)
        self.output_range = float(output_range)
        self.input_range_enum = _range_to_enum(self.input_range)
        self.output_range_enum = _range_to_enum(self.output_range)

        self.input_probe = float(input_probe)
        self.output_probe = float(output_probe)
        self.requested_resolution_bits = int(resolution_bits)
        self.resolution_bits = int(resolution_bits)
        self.allow_resolution_fallback = bool(allow_resolution_fallback)
        self.bandwidth_limit_name = str(bandwidth_limit).strip().lower()
        self.sample_rate = float(sample_rate)
        self.capture_time = float(capture_time)

        if self.input_probe <= 0 or self.output_probe <= 0:
            raise ValueError("Probe attenuation factors must be greater than zero")

        if self.sample_rate <= 0:
            raise ValueError("Sample rate must be greater than zero")

        if self.capture_time <= 0:
            raise ValueError("Capture time must be greater than zero")

        if self.resolution_bits not in (8, 10, 12):
            raise ValueError("PicoScope resolution must be 8, 10, or 12 bits")

        bandwidth_values = {
            "full": "PICO_BW_FULL",
            "20mhz": "PICO_BW_20MHZ",
            "200mhz": "PICO_BW_200MHZ",
        }
        if self.bandwidth_limit_name not in bandwidth_values:
            raise ValueError(
                "PicoScope bandwidth_limit must be 'full', '20MHz', or '200MHz'"
            )
        self.bandwidth = picoEnum.PICO_BANDWIDTH_LIMITER[
            bandwidth_values[self.bandwidth_limit_name]
        ]

        self.resolution = picoEnum.PICO_DEVICE_RESOLUTION[
            f"PICO_DR_{self.resolution_bits}BIT"
        ]
        self.data_type = picoEnum.PICO_DATA_TYPE["PICO_INT16_T"]
        self.ratio_mode = picoEnum.PICO_RATIO_MODE["PICO_RATIO_MODE_RAW"]
        self.clear_all = picoEnum.PICO_ACTION["PICO_CLEAR_ALL"]
        self.add = picoEnum.PICO_ACTION["PICO_ADD"]

        self.timebase = None
        self.sample_interval = None
        self.samples = None
        self.adc_max = None
        self.enabled_channels = None

    @staticmethod
    def _resolution_enum(bits):
        return picoEnum.PICO_DEVICE_RESOLUTION[f"PICO_DR_{bits}BIT"]

    @staticmethod
    def _status_name(status):
        for name, value in PICO_STATUS.items():
            if value == status:
                return name
        return "UNKNOWN_STATUS"

    def check(self, status, name):
        if status != PICO_STATUS["PICO_OK"]:
            raise RuntimeError(
                f"{name} failed: {self._status_name(status)} ({status})"
            )

    #######################################################################
    # Connection and configuration
    #######################################################################

    def connect(self):
        status = ps6000a.ps6000aOpenUnit(
            byref(self.handle),
            None,
            self.resolution,
        )
        self.check(status, "OpenUnit")
        self.connected = True

        try:
            print(f"PicoScope connected. Handle={self.handle.value}")
            self.configure()
        except Exception:
            self.close()
            raise

    def configure(self):
        coupling = picoEnum.PICO_COUPLING["PICO_DC"]

        status = ps6000a.ps6000aSetChannelOn(
            self.handle,
            self.input_channel,
            coupling,
            self.input_range_enum,
            0.0,
            self.bandwidth,
        )
        self.check(status, f"Channel {self.input_channel_name}")

        status = ps6000a.ps6000aSetChannelOn(
            self.handle,
            self.output_channel,
            coupling,
            self.output_range_enum,
            0.0,
            self.bandwidth,
        )
        self.check(status, f"Channel {self.output_channel_name}")

        enabled_channels = (
            (1 << self.input_channel)
            | (1 << self.output_channel)
        )
        self.enabled_channels = enabled_channels
        requested_interval = 1.0 / self.sample_rate
        timebase, available_interval = self._select_timebase(
            enabled_channels,
            requested_interval,
        )

        adc_min = c_int16()
        adc_max = c_int16()
        status = ps6000a.ps6000aGetAdcLimits(
            self.handle,
            self.resolution,
            byref(adc_min),
            byref(adc_max),
        )
        self.check(status, "GetAdcLimits")
        self.adc_max = float(adc_max.value)

        self.timebase = timebase.value
        self.sample_interval = available_interval.value
        self.samples = max(
            1,
            int(round(self.capture_time / self.sample_interval)),
        )

        self._validate_sample_count(self.samples)

        actual_rate = 1.0 / self.sample_interval
        actual_duration = self.samples * self.sample_interval
        print(
            "PicoScope acquisition: "
            f"{self.samples} samples at {actual_rate / 1e6:.6g} MS/s "
            f"({actual_duration * 1e3:.6g} ms), "
            f"bandwidth {self.bandwidth_limit_name}"
        )

    def _validate_sample_count(self, samples):
        """Check that block memory is sufficient for a requested capture."""

        interval_ns = c_double()
        max_samples = c_uint64()
        status = ps6000a.ps6000aGetTimebase(
            self.handle,
            self.timebase,
            int(samples),
            byref(interval_ns),
            byref(max_samples),
            0,
        )
        self.check(status, "GetTimebase")

        if samples > max_samples.value:
            raise RuntimeError(
                f"Requested {samples} samples, but the selected "
                f"PicoScope configuration allows {max_samples.value}"
            )

        # Use the interval returned for the active configuration.
        self.sample_interval = interval_ns.value * 1e-9

    def _select_timebase(self, enabled_channels, requested_interval):
        """
        Find a supported timebase, reducing FlexRes resolution if necessary.

        Some 6000E models do not permit every two-channel combination in
        12-bit mode. The driver reports status 396 for that case. Retrying at
        10-bit keeps the same physical channels and is preferable to aborting
        the complete measurement before the supply output is enabled.
        """

        candidates = [self.requested_resolution_bits]
        if self.allow_resolution_fallback:
            candidates.extend(
                bits
                for bits in (10, 8)
                if bits < self.requested_resolution_bits
            )

        channel_combination_error = PICO_STATUS.get(
            "PICO_CHANNEL_COMBINATION_NOT_VALID_IN_THIS_RESOLUTION",
            396,
        )

        for index, bits in enumerate(candidates):
            resolution = self._resolution_enum(bits)

            if resolution != self.resolution:
                status = ps6000a.ps6000aSetDeviceResolution(
                    self.handle,
                    resolution,
                )
                self.check(status, f"SetDeviceResolution({bits}-bit)")

            timebase = c_uint32()
            available_interval = c_double()
            status = ps6000a.ps6000aNearestSampleIntervalStateless(
                self.handle,
                enabled_channels,
                requested_interval,
                resolution,
                byref(timebase),
                byref(available_interval),
            )

            if status == PICO_STATUS["PICO_OK"]:
                self.resolution_bits = bits
                self.resolution = resolution
                if bits != self.requested_resolution_bits:
                    print(
                        "PicoScope warning: "
                        f"{self.requested_resolution_bits}-bit mode is not "
                        f"valid for channels {self.input_channel_name}+"
                        f"{self.output_channel_name}; using {bits}-bit mode."
                    )
                return timebase, available_interval

            can_retry = (
                status == channel_combination_error
                and index + 1 < len(candidates)
            )
            if not can_retry:
                self.check(status, "NearestSampleIntervalStateless")

        raise RuntimeError("No compatible PicoScope acquisition mode found")

    #######################################################################
    # Block capture
    #######################################################################

    def _prepare_capture_timebase(
        self,
        capture_time,
        sample_rate=None,
        max_samples=None,
    ):
        """
        Select a timebase for one block.

        Slow transient records are allowed to use a lower sample rate so their
        complete evolution fits into a bounded number of samples. Ordinary
        captures continue to use the configured SAMPLE_RATE.
        """

        requested_rate = (
            self.sample_rate if sample_rate is None else float(sample_rate)
        )
        if requested_rate <= 0:
            raise ValueError("sample_rate must be greater than zero")
        if max_samples is not None:
            max_samples = int(max_samples)
            if max_samples < 2:
                raise ValueError("max_samples must be at least two")
            requested_rate = min(
                requested_rate,
                0.95 * max_samples / float(capture_time),
            )

        timebase = c_uint32()
        available_interval = c_double()
        status = ps6000a.ps6000aNearestSampleIntervalStateless(
            self.handle,
            self.enabled_channels,
            1.0 / requested_rate,
            self.resolution,
            byref(timebase),
            byref(available_interval),
        )
        self.check(status, "NearestSampleIntervalStateless(capture)")

        self.timebase = timebase.value
        self.sample_interval = available_interval.value
        samples = max(2, int(round(float(capture_time) / self.sample_interval)))
        if max_samples is not None:
            samples = min(samples, max_samples)
        self._validate_sample_count(samples)
        return samples

    def capture(
        self,
        capture_time=None,
        action=None,
        action_delay=0.0,
        sample_rate=None,
        max_samples=None,
    ):
        """
        Capture both channels, optionally executing an action during the block.

        The action mechanism is used for supply startup/shutdown captures. It
        deliberately does not treat the serial command time as the electrical
        transition time; analysis references the measured Channel-A edge.
        """

        if not self.connected:
            raise RuntimeError("PicoScope is not connected")

        capture_time = self.capture_time if capture_time is None else float(capture_time)
        action_delay = float(action_delay)
        if capture_time <= 0:
            raise ValueError("capture_time must be greater than zero")
        if action_delay < 0 or action_delay >= capture_time:
            raise ValueError("action_delay must be >=0 and shorter than capture_time")

        samples = self._prepare_capture_timebase(
            capture_time,
            sample_rate=sample_rate,
            max_samples=max_samples,
        )
        buffer_a = np.zeros(samples, dtype=np.int16)
        buffer_b = np.zeros(samples, dtype=np.int16)

        first_action = self.clear_all | self.add

        status = ps6000a.ps6000aSetDataBuffer(
            self.handle,
            self.input_channel,
            buffer_a.ctypes.data_as(c_void_p),
            samples,
            self.data_type,
            0,
            self.ratio_mode,
            first_action,
        )
        self.check(status, f"Channel {self.input_channel_name} buffer")

        status = ps6000a.ps6000aSetDataBuffer(
            self.handle,
            self.output_channel,
            buffer_b.ctypes.data_as(c_void_p),
            samples,
            self.data_type,
            0,
            self.ratio_mode,
            self.add,
        )
        self.check(status, f"Channel {self.output_channel_name} buffer")

        time_indisposed = c_double()
        status = ps6000a.ps6000aRunBlock(
            self.handle,
            0,
            samples,
            self.timebase,
            byref(time_indisposed),
            0,
            None,
            None,
        )
        self.check(status, "RunBlock")

        if action is not None:
            if action_delay:
                time.sleep(action_delay)
            try:
                action()
            except Exception:
                ps6000a.ps6000aStop(self.handle)
                raise

        ready = c_int16(0)
        deadline = time.monotonic() + max(5.0, 5.0 * capture_time)

        while ready.value == 0:
            status = ps6000a.ps6000aIsReady(
                self.handle,
                byref(ready),
            )
            self.check(status, "IsReady")

            if time.monotonic() > deadline:
                ps6000a.ps6000aStop(self.handle)
                raise TimeoutError("PicoScope acquisition timed out")

            time.sleep(0.001)

        n_samples = c_uint64(samples)
        overflow = c_int16()
        status = ps6000a.ps6000aGetValues(
            self.handle,
            0,
            byref(n_samples),
            1,
            self.ratio_mode,
            0,
            byref(overflow),
        )
        self.check(status, "GetValues")

        overflowed = []
        for name, channel in (
            (self.input_channel_name, self.input_channel),
            (self.output_channel_name, self.output_channel),
        ):
            if overflow.value & (1 << channel):
                overflowed.append(name)

        if overflowed:
            channels = ", ".join(overflowed)
            raise RuntimeError(
                f"PicoScope over-range on channel(s) {channels}. "
                "Increase the corresponding range in config.py."
            )

        count = n_samples.value
        voltage_in = (
            buffer_a[:count].astype(np.float64)
            / self.adc_max
            * self.input_range
            * self.input_probe
        )
        voltage_out = (
            buffer_b[:count].astype(np.float64)
            / self.adc_max
            * self.output_range
            * self.output_probe
        )

        return voltage_in, voltage_out

    #######################################################################
    # Shutdown
    #######################################################################

    def close(self):
        if self.connected:
            ps6000a.ps6000aCloseUnit(self.handle)
            self.connected = False
