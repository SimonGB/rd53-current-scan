#!/usr/bin/env python3
"""RD53 v2 shunt-board current sweep, HMP4040 CH2 and PicoScope A/B."""
import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
import sys
import time
import numpy as np


def current_points(start, stop, step):
    """Exact uniform steps; include stop only when it lies on the grid."""
    if not all(math.isfinite(x) for x in (start, stop, step)):
        raise ValueError('Sweep values must be finite')
    if min(start, stop) <= 0 or step <= 0 or stop < start:
        raise ValueError('Require 0 < start <= stop and step > 0 (amperes)')
    a, b, s = map(lambda x: Decimal(str(x)), (start, stop, step))
    count = int((b-a)//s)+1
    if count > 100000:
        raise ValueError('Sweep exceeds 100000 points')
    return [float(a+i*s) for i in range(count)]


def plot(rows, directory):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(8, 7), sharex=True)
    for direction in dict.fromkeys(r['direction'] for r in rows):
        subset = [r for r in rows if r['direction'] == direction]
        x = [r['current_measured_A']*1000 for r in subset]
        for ax, signal, label in zip(axes, ('reference', 'output'),
                                    ('Reference CH1/A', 'Output CH2/B')):
            ax.errorbar(x, [r[f'{signal}_mean_V'] for r in subset],
                        yerr=[r[f'{signal}_capture_std_V'] for r in subset],
                        marker='o', markersize=3, label=direction, capsize=2)
            ax.set_ylabel(f'{label} [V]')
            ax.grid(alpha=.3)
            ax.legend()
    axes[-1].set_xlabel('Measured HMP4040 CH2 current [mA]')
    fig.suptitle('RD53 v2 shunt-board current scan\nError bars: spread of repeated capture means')
    fig.tight_layout()
    fig.savefig(directory/'voltages.png', dpi=160)
    fig.savefig(directory/'voltages.pdf')
    plt.close(fig)


class SimSupply:
    identity = 'SIMULATED HMP4040'
    def connect(self): pass
    def prepare_scan(self, voltage, current):
        self.voltage, self.current = voltage, current
    def set_current_limit(self, current): self.current = current
    def readback(self): return self.current, min(self.voltage, 1.2)
    def output_off(self): pass
    def close(self): pass


class SimScope:
    resolution_bits = 12
    sample_interval = 1e-6
    def __init__(self, supply):
        self.supply = supply
        self.rng = np.random.default_rng(53)
    def connect(self): pass
    def capture(self):
        gain = min(1, self.supply.current/.025)
        return (self.rng.normal(.6*gain, .0002, 1000),
                self.rng.normal(min(self.supply.voltage, 1.2*gain), .0005, 1000))
    def close(self): pass


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--start', type=float, required=True, help='Starting current [A], >0')
    p.add_argument('--stop', type=float, required=True, help='Maximum current [A]')
    p.add_argument('--step', type=float, required=True, help='Current step [A]')
    p.add_argument('--voltage-limit', type=float, required=True, help='Board-safe compliance voltage [V]')
    p.add_argument('--max-current', type=float, required=True, help='Explicit board-safe maximum current [A]')
    p.add_argument('--port', default='/dev/ttyUSB1')
    p.add_argument('--baudrate', type=int, default=9600)
    p.add_argument('--channel', type=int, choices=range(1, 5), default=2)
    p.add_argument('--settle', type=float, default=1, help='Settling delay per current point [s]')
    p.add_argument('--captures', type=int, default=10)
    p.add_argument('--capture-time', type=float, default=.02)
    p.add_argument('--sample-rate', type=float, default=1e6)
    p.add_argument('--reference-range', type=float, default=2)
    p.add_argument('--output-range', type=float, default=2)
    p.add_argument('--reference-probe', type=float, default=1)
    p.add_argument('--output-probe', type=float, default=1)
    p.add_argument('--resolution', type=int, choices=(8, 10, 12), default=12)
    p.add_argument('--reverse', action='store_true', help='Also scan down to start')
    p.add_argument('--save-waveforms', action='store_true')
    p.add_argument('--simulate', action='store_true', help='No instruments used')
    p.add_argument('--name', default='rd53_v2')
    p.add_argument('--output', type=Path, default=Path('results'))
    return p


def run(args, supply=None, scope=None):
    points = current_points(args.start, args.stop, args.step)
    positive = ('voltage_limit', 'max_current', 'capture_time', 'sample_rate',
                'reference_probe', 'output_probe', 'reference_range', 'output_range')
    if any(not math.isfinite(getattr(args, n)) or getattr(args, n) <= 0 for n in positive):
        raise ValueError('Voltage, current, acquisition and probe limits must be positive and finite')
    if args.stop > args.max_current or args.max_current > 10 or args.voltage_limit > 32:
        raise ValueError('Sweep exceeds maximum current or HMP4040 10 A / 32 V limits')
    if args.captures < 1 or not math.isfinite(args.settle) or args.settle < 0:
        raise ValueError('Require captures >=1 and finite settle >=0')
    if args.sample_rate*args.capture_time*args.captures > 20000000:
        raise ValueError('Limit total nominal samples per point to 20 million')
    if args.sample_rate*args.capture_time > 2000000:
        raise ValueError('Limit each capture to <=2 million nominal samples')
    if not args.name or args.name in ('.', '..') or '/' in args.name or '\\' in args.name:
        raise ValueError('Name must be a single directory component')
    if supply is None:
        if args.simulate:
            supply = SimSupply()
            scope = SimScope(supply)
        else:
            from instruments import HMP4040
            from picoscope import PicoScope
            supply = HMP4040(port=args.port, baudrate=args.baudrate,
                             channel=args.channel, command_delay=.1,
                             voltage_settling_time=.5, output_on_verify_delay=.05)
            scope = PicoScope(input_channel='A', output_channel='B',
                              input_range=args.reference_range, output_range=args.output_range,
                              input_probe=args.reference_probe, output_probe=args.output_probe,
                              resolution_bits=args.resolution, sample_rate=args.sample_rate,
                              capture_time=args.capture_time)
    if scope is None:
        raise ValueError('Scope required with injected supply')
    directory = args.output/args.name/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    directory.mkdir(parents=True, exist_ok=False)
    metadata = {**vars(args), 'output':str(args.output), 'status':'initializing',
                'points_A':points, 'simulation':args.simulate,
                'channel_mapping':{'HMP':args.channel, 'reference':'A', 'output':'B'}}
    rows, armed, shutdown_errors = [], False, []
    failure = None
    csvfile = None
    try:
        # Scope must be ready before the supply is changed.
        scope.connect()
        supply.connect()
        metadata['supply_identity'] = supply.identity
        metadata['actual_scope_resolution_bits'] = scope.resolution_bits
        armed = True
        supply.prepare_scan(args.voltage_limit, points[0])
        sequence = [('up', x) for x in points]
        if args.reverse:
            sequence += [('down', x) for x in reversed(points)]
        for index, (direction, current) in enumerate(sequence):
            supply.set_current_limit(current)
            time.sleep(0 if args.simulate else args.settle)
            before, _ = supply.readback()
            captures = [scope.capture() for _ in range(args.captures)]
            after, voltage = supply.readback()
            measured = (before+after)/2
            if max(before, after) > args.max_current + max(.0001, .02*args.max_current):
                raise RuntimeError('Measured supply current exceeds the configured maximum')
            if not all(math.isfinite(x) for x in (before, after, voltage)):
                raise RuntimeError('Invalid supply readback')
            row = dict(point=index, direction=direction, timestamp_UTC=datetime.now(timezone.utc).isoformat(),
                       current_set_A=current, current_measured_A=measured,
                       current_before_A=before, current_after_A=after, supply_voltage_V=voltage,
                       current_tracking_ok=abs(measured-current) <= max(.0001, .02*current))
            for i, signal in enumerate(('reference', 'output')):
                waves = [np.asarray(c[i], dtype=float) for c in captures]
                if any(w.size < 2 or not np.isfinite(w).all() for w in waves):
                    raise RuntimeError(f'Invalid {signal} waveform')
                means = [float(w.mean()) for w in waves]
                row[f'{signal}_mean_V'] = float(np.mean(means))
                row[f'{signal}_capture_std_V'] = float(np.std(means, ddof=1)) if len(means)>1 else 0.
                row[f'{signal}_waveform_rms_noise_V'] = float(np.sqrt(np.mean([np.var(w) for w in waves])))
                row[f'{signal}_max_capture_p2p_V'] = float(max(np.ptp(w) for w in waves))
            if args.save_waveforms:
                data = {f'{signal}_{j}':c[i] for j,c in enumerate(captures)
                        for i,signal in enumerate(('reference', 'output'))}
                np.savez_compressed(directory/f'waveforms_{index:05d}.npz',
                                    sample_interval_s=scope.sample_interval, **data)
            if csvfile is None:
                csvfile = (directory/'results.csv').open('w', newline='')
                writer = csv.DictWriter(csvfile, fieldnames=list(row))
                writer.writeheader()
            writer.writerow(row)
            csvfile.flush()
            rows.append(row)
            print(f'{direction}: set {current*1000:.3f} mA, measured {measured*1000:.3f} mA; '
                  f'Vref={row["reference_mean_V"]:.6f} V, Vout={row["output_mean_V"]:.6f} V'
                  + ('' if row['current_tracking_ok'] else ' [CURRENT NOT REACHED]'), flush=True)
        metadata['status'] = 'complete'
    except BaseException as exc:
        metadata['status'] = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'
        metadata['error'] = f'{type(exc).__name__}: {exc}'
        failure = exc
    finally:
        if csvfile is not None:
            csvfile.close()
        if armed:
            try: supply.output_off()
            except BaseException as exc: shutdown_errors.append(f'OUTPUT OFF FAILED: {exc}')
        for instrument in (supply, scope):
            try: instrument.close()
            except BaseException as exc: shutdown_errors.append(f'Close failed: {exc}')
        metadata['shutdown_errors'] = shutdown_errors
        if shutdown_errors:
            metadata['status'] = 'shutdown_failed'
            for error in shutdown_errors: print(error, file=sys.stderr)
        metadata['completed_points'] = len(rows)
        (directory/'metadata.json').write_text(json.dumps(metadata, indent=2)+'\n')
        if rows:
            try: plot(rows, directory)
            except Exception as exc:
                print(f'Plot failed; CSV retained: {exc}', file=sys.stderr)
                if failure is None: failure = exc
        print(f'Results: {directory}')
    if failure is not None: raise failure
    if shutdown_errors: raise RuntimeError('; '.join(shutdown_errors))
    return directory


if __name__ == '__main__':
    try:
        run(parser().parse_args())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
