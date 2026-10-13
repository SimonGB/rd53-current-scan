# RD53 v2 shunt-board current scan

Scan the current supplied to a shunt-regulator testboard and measure its
reference and output voltage. Reuses the HMP4040 and PicoScope 6426E / PS6000A
instrument code from the charge-pump measurement project
(`chargepump_measurement_hmp_settle_guard_20260731.zip`).
Hardware operation has not yet been verified on these RD53 boards.

## Connections

| Instrument | Channel | Connection |
| --- | --- | --- |
| Hameg / R&S HMP4040 | CH2 | Testboard power input and return |
| PicoScope 6426E | CH1 / A | Reference voltage relative to board return |
| PicoScope 6426E | CH2 / B | Output voltage relative to board return |

Use the board's documented input terminals; its pinout is not assumed here.
Both scope channels use DC coupling, high impedance and a configurable probe
attenuation (default x1). Both probe grounds go to the same board return.
This setup is for a single board at a common scope ground; ordinary grounded
probes must not be attached to different floating returns in a serial chain.
Close PicoScope's desktop application before opening the SDK connection.

## Install

Python 3.10+:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Hardware runs additionally require Pico Technology's native **PS6000A** SDK
library (`libps6000a` on Linux), installed from the vendor, and USB/serial access
permissions. `pip install picosdk` installs the Python wrapper, not the native
library. This driver targets the earlier **6426E**; other PicoScope families
require a different driver/API. Windows can use `--port COM3` (choose the actual
port) and `.venv\Scripts\activate`.

## First try: simulated scan

```bash
python scan.py --simulate --start 0.005 --stop 0.050 --step 0.005 \
  --voltage-limit 1.5 --max-current 0.050 --name demo --reverse
```

These numbers are **simulation examples, not validated RD53 board ratings**.
The program never energizes hardware in simulation mode.

## Hardware scan

Set values from the testboard documentation / your established safe operating
limits, then run (all current arguments are in amperes):

```bash
python scan.py --port /dev/ttyUSB1 --channel 2 \
  --start START_A --stop STOP_A --step STEP_A \
  --voltage-limit SAFE_COMPLIANCE_V --max-current SAFE_MAX_A \
  --settle 1 --captures 10 --name board_01
```

Replace the uppercase placeholders with numeric values. The compliance voltage
is the Hameg voltage setpoint: enough headroom for the regulator but within the
board's safe limit. Setting `CURR` controls the current limit. The supply delivers
the requested current only if the load can draw it within the compliance voltage.
Consequently the CSV records actual current before and after the captures;
plots use their average. `CURRENT NOT REACHED` flags deviation greater than
max(0.1 mA, 2% of the current setting); it is a tracking diagnostic, not a
calibrated CC/CV status or a board pass/fail criterion.

Start and stop must be positive. Steps are uniform; the stop is included only
when it lies on the step grid. `--reverse` repeats the highest point and then
scans down, useful for checking hysteresis. Add `--save-waveforms` for every raw
capture; use `--reference-probe 10 --output-probe 10` for x10 probes. Input ranges
are BNC full-scale ±voltages, before multiplying by probe attenuation.
Supported ranges: 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20 V.

The scope connects before power is enabled. The HMP adapter verifies setpoints,
uses staged delays, avoids the old driver's global reset, and disables the
selected output on completion, acquisition failure or Ctrl+C. It does not change
other channels' setpoints/selections. When the master is initially off, it refuses
to enable it if another channel is selected, since that would energize that
channel too. Leave supply front-panel controls untouched during a run.
Output-disable failures are reported as errors: manually verify power is off.
Termination by SIGKILL, loss of PC power or loss of communication cannot guarantee
shutdown, so the programmed hardware current and voltage limits remain essential.

## Saved results

Each run creates `results/<name>/<UTC timestamp>/`:

- `results.csv`: requested/measured current, supply voltage, reference/output
  mean, spread of repeated capture means, waveform RMS noise and peak-to-peak.
- `voltages.png` and `voltages.pdf`: two panels, reference/output versus actual
  supply current, with separate up/down curves.
- `metadata.json`: settings, actual scope resolution, supply identity,
  completion status and shutdown errors. Simulation is explicitly recorded.
- Optional `waveforms_*.npz`: voltage arrays and sample interval in seconds.

Error bars are the sample standard deviation of repeated **capture means**;
they are not the total measurement uncertainty. Waveform RMS noise is reported
separately, and neither quantity includes calibration/probe systematic errors.
The driver rejects scope over-range instead of saving clipped voltage readings.
CSV is flushed after each point, and partial results/plots survive ordinary
exceptions or Ctrl+C. The script does not determine regulator pass/fail thresholds.

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests cover the sweep grid, limits, reverse scan and shutdown on errors/Ctrl+C.
A simulation exercises CSV and plot generation without Pico SDK or hardware.

## References

- [R&S HMP Series manual](https://scdn.rohde-schwarz.com/ur/pws/dl_downloads/pdm/cl_manuals/user_manual/1178_6833_01/HMPSeries_UserManual_en_04.pdf)
- [PicoScope PS6000A programmer's guide](https://www.picotech.com/download/manuals/picoscope-6000-series-a-api-programmers-guide.pdf)
# rd53-current-scan
# rd53-current-scan
