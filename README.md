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


## Desktop test station and Venelin ADC support

Update and launch:

```bash
git pull
python -m pip install -r requirements-gui.txt
python gui.py
```

The GUI starts in **simulation** mode. Set the Hameg device (default now
`/dev/hmp4040-1`), choose Pico or Venelin, and assign physical measurement inputs
to board/testpoint pairs. Start/stop controls, live plots, voltage readouts and
progress are included. Current fields in the GUI are **mA**; command-line current
arguments remain **A**. Configure Pico probe factors explicitly to match x1/x10
switches. Input assignments document your physical wiring; software does not
move probes or operate a hardware multiplexer.

- Add independent RD53 board IDs and testpoint names.
- Click a testpoint to select its assignment row; the row fields are editable.
- Load a board photo, enable “Place / move testpoints on click”, and click to add
  a named marker or move an existing one. Coordinates are saved with the profile.
- Save/load JSON wiring profiles, including probe factors, ranges and ADC paths.
  Photo paths are absolute local paths; copy photos separately when moving machines.
- Start a simulated run first. Hardware runs require checking the wiring/limits
  checkbox. Stop requests cooperative shutdown without force-killing the worker;
  closing the window while running requests Stop and defers closure until stopped.
  A single Pico block can take until its acquisition timeout to return.

The default graphic is a **schematic**, not a verified drawing of your PCB.
Vref/Vout are measurement labels, not inferred connector or TP numbers. Supply
safe ratings and physical testpoint positions from your actual board documentation.

### Venelin setup

Use the updated `SimonGB/pmod_adc` checkout and build its C++ helpers first.
The scan uses `install/bin/pm_adcs_quick_ini` for normal initialization at the
selected sample rate and `pm_adcs_startl` for fresh finite records. Set its serial
port at build time as described in that project's README. Close its GUI and
other acquisition processes before running this station; helper initialization
stops an existing board stream.

In the Venelin GUI tab choose that repository's root, `exe/pgm/config.toml`,
and the decoded data path (normally `exe/pgm/fadc.dat`). The helper's compiled
output path must match the selected data path. Select a sample rate of 250,
500, 1000, 2000, 4000, 8000, 16000 or 32000 S/s. The new adapter controls only
one ADC motherboard and its PMOD1..6 modules.

Channel IDs are physical `PMODn_Ch0..7` identities. The adapter derives sparse
acquisition masks, then matches decoded file-header identities to channels;
it never assumes that CSV column number is the global channel number. Input
profiles come from the TOML `[BoardInputs]` and `[Calibration]` sections:

- `dV`: signed ADC code × connector full-scale voltage / 2^(bits−1).
- `I`: convert shunt voltage, then divide by configured measurement shunt ohms.
- `aV`: use its configured absolute-voltage full-scale span.

Fitted types/gains must agree with the hardware. Software cannot turn an I input
into a dV input. The adapter applies nominal configured scaling; it **does not
import the PMOD GUI's separate two-point calibration records**. It does not run
zero calibration. ADC full-scale clipping, missing inputs, malformed/short
captures, acquisition timeouts and unchanged output files cause an error and
supply shutdown. A frame's physical inputs are sampled together; consecutive
captures and Pico versus ADC runs are not synchronized with each other.

Example using a wiring profile (replace its ADC paths first):

```bash
python scan.py --profile profiles/venelin-example.json \
  --port /dev/hmp4040-1 --start 0.005 --stop 0.050 --step 0.005 \
  --voltage-limit 3.5 --max-current 0.450 \
  --sample-rate 1000 --capture-time 0.1 --captures 10 \
  --name board1_adc --reverse --save-waveforms
```

These current/voltage values reflect the earlier bench command and are not
universal RD53 ratings. `profiles/pico-example.json` preserves A/B wiring with
x1 factors; adjust them if your probes are x10. Add `--simulate` to test either
profile without hardware. Original scans without `--profile` remain supported.

### Multiple RD53 boards

Multiple board IDs may be monitored during one shared Hameg-channel current
sweep. The Pico adapter currently supports **two total mapped inputs**; these
can be on different boards only when their measurement returns permit it. The
Venelin adapter can select up to **48 inputs** across six PMOD modules, subject
to installed modules and their fitted input types. This update does not control
multiple independent supplies or sequence separate boards with relays.

For a serial chain, verify differential-input common mode, amplifier supply
rails and total chain voltage against the actual ADC board documentation. The
board quickstart gives a nominal differential common-mode range of 0..27 V;
ordinary Pico probes share ground, and ADC inputs are not isolated. Physical
wiring must not short floating board returns together.

### Mapped output and fluctuations

Mapped runs create `measurements.csv` in **long format**, one row per board /
testpoint / current point, plus `mapped_voltages.png/.pdf` and metadata containing
the complete wiring profile, input scaling and run status.

- `capture_std`: standard deviation between repeated record means (plot bars).
  Empty when only one capture exists, since that statistic cannot be estimated.
- `sample_std`: sample standard deviation across all recorded samples, including
  between-record shifts.
- `within_capture_rms`: RMS fluctuations around each record's own mean.
- `peak_to_peak`: maximum minus minimum across all captures at that point.

V channels use V and I channels use A for these statistics. None of these is a
calibrated total measurement uncertainty. Samples can be correlated; increasing
sample count alone does not eliminate systematic errors. Raw `.npz` files store
scaled signals and nominal sample interval; acquisition gaps between records
are not part of that interval.

### Useful next additions

Prioritize a measured gain/offset calibration with provenance; voltage/temperature
abort thresholds; repeat-scan overlays and board comparison; automatic settling
detection; long-duration drift monitoring; and load-step measurements. Later,
add topology profiles for parallel/series/mixed configurations, independent supply
channels and hardware switching only after the corresponding wiring and hardware
interfaces are defined.

### Linux GUI startup: GNOME antialiasing schema error

If startup aborts with `org.gnome.settings-daemon.plugins.xsettings` missing
`antialiasing`, the desktop GTK/GNOME theme integration is incompatible with
its loaded settings schema. On Linux this GUI now selects Qt generic theming
and Fusion style before constructing QApplication. These settings apply only
to this process; the system desktop configuration is unchanged.

Update with `git pull` and launch `python gui.py`. For an older checkout, try:

```bash
QT_QPA_PLATFORMTHEME=generic QT_STYLE_OVERRIDE=Fusion python gui.py
```

To explicitly opt back into native desktop themes, use
`RD53_NATIVE_QT_THEME=1 python gui.py`. This bypass has been startup-tested
in an offscreen environment; the reported desktop crash cannot be reproduced
without the bench computer\x27s library/schema combination.
