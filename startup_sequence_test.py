#!/usr/bin/env python3
"""Investigate RD53 Shunt-LDO startup history using bounded HMP4040 steps.

This tests instrument-command sequences, NOT calibrated analogue rise time.
For fast startup waveforms, use a synchronized scope/current-sense recording.
No physical output is energized without --arm. Inspect the board's safe
compliance voltage/current limits, shared oscilloscope grounds and wiring.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

import numpy as np

from mapping import default_profile, load_profile
from scan import SimSupply
from station import MappedPico, SimMapped


@dataclass(frozen=True)
class Step:
    current_ma: float
    dwell_s: float


def staircase(target_ma=80.0, step_ma=5.0, dwell_s=1.0):
    """Generate a staircase INCLUDING the target current without overshoot."""
    if not (0 < step_ma <= target_ma):
        raise ValueError('Require 0 < staircase step <= target')
    values = np.arange(step_ma, target_ma, step_ma, dtype=float).tolist()
    values.append(float(target_ma))
    return [Step(float(value), dwell_s) for value in values]


def sequences(mode, direct_ma, target_ma, step_ma, dwell_s):
    direct = [Step(direct_ma, dwell_s)]
    ramp = staircase(target_ma, step_ma, dwell_s)
    if mode == 'direct':
        return [('direct', direct)]
    if mode == 'staircase':
        return [('staircase', ramp)]
    if mode == 'both':
        return [('staircase', ramp), ('direct', direct)]
    if mode == 'recovery':
        return [('staircase', ramp), ('direct_after_reset', direct)]
    raise ValueError('Unknown sequence mode')


def validate_args(args, profile):
    if args.simulate and args.arm:
        raise ValueError('--arm and --simulate are mutually exclusive')
    if not args.simulate and not args.arm:
        raise ValueError('Hardware is disabled: specify --arm after checking your circuit limits, or --simulate')
    if args.arm and args.profile is None:
        raise ValueError('A verified wiring --profile is required for real hardware')
    if profile['backend'] != 'pico':
        raise ValueError('Initial startup-step test supports mapped PicoScope input channels only')
    if not any('out' in item['testpoint'].lower() for item in profile['mappings']):
        raise ValueError('Map Vout to a PicoScope input to classify startup')
    values = [args.voltage_limit, args.max_current_ma, args.direct_ma,
              args.target_ma, args.step_ma, args.dwell, args.off_time,
              args.capture_time, args.sample_rate, args.startup_threshold_v]
    if any(not math.isfinite(v) for v in values):
        raise ValueError('All numeric settings must be finite')
    if not (0 < args.voltage_limit <= 3.5):
        raise ValueError('Voltage compliance must be explicitly set within (0, 3.5] V')
    if not (0 < args.max_current_ma <= 2000):
        raise ValueError('Current safety limit must be within (0, 2000] mA')
    if not (0 < args.direct_ma <= args.max_current_ma and
            0 < args.target_ma <= args.max_current_ma and
            0 < args.step_ma <= args.target_ma):
        raise ValueError('Every programmed current must be positive and within --max-current-ma')
    if not (0 <= args.dwell <= 300 and 0 <= args.off_time <= 300):
        raise ValueError('Dwell and off-time must be between 0 and 300 seconds')
    if not (0 < args.capture_time <= 2 and 0 < args.sample_rate <= 10_000_000):
        raise ValueError('Capture length or sample rate outside bounded test settings')
    if args.capture_time * args.sample_rate > 2_000_000:
        raise ValueError('Maximum two million capture samples per step')
    if not (0 < args.startup_threshold_v <= args.voltage_limit):
        raise ValueError('Startup threshold must be positive and below voltage compliance')
    if not (1 <= args.repeats <= 100):
        raise ValueError('Repeats must be 1–100')
    if not args.name or '/' in args.name or '\\' in args.name or args.name in ('.','..'):
        raise ValueError('Invalid run name')


def make_reader(args, supply, profile):
    if args.simulate:
        return SimMapped(supply, profile['mappings'])
    # Matched to station.py channel/probe mapping and PicoScope settings.
    reader_args = argparse.Namespace(resolution=args.resolution,
        sample_rate=args.sample_rate, capture_time=args.capture_time)
    return MappedPico(reader_args, profile['mappings'])


def capture_step(reader, mappings, repeats=1):
    channels = {}
    for _ in range(repeats):
        result = reader.capture()
        for mapping in mappings:
            channel = mapping['channel']
            channels.setdefault(channel, []).append(np.asarray(result[channel], dtype=float))
    return {mapping['testpoint']: float(np.mean(np.concatenate(channels[mapping['channel']])))
            for mapping in mappings}


def perform(args, profile, supply=None, reader=None, sleep=time.sleep):
    """Execute startup trials. Supply and reader can be injected for tests."""
    validate_args(args, profile)
    trial_plan = sequences(args.sequence, args.direct_ma, args.target_ma,
                           args.step_ma, args.dwell)
    out = Path(args.output) / args.name / (
        'startup_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
    out.mkdir(parents=True, exist_ok=False)
    config = {key: (str(value) if isinstance(value, Path) else value)
              for key,value in vars(args).items()}
    metadata = dict(settings=config, profile=profile, status='starting',
                    completed_steps=0, trials=[], note=(
                     'HMP current setpoint steps; rise time is NOT controlled or measured'))
    (out/'metadata.json').write_text(json.dumps(metadata,indent=2))
    if supply is None:
        if args.simulate:
            supply = SimSupply()
        else:
            from instruments import HMP4040
            supply = HMP4040(port=args.port,baudrate=args.baudrate,
                             channel=args.channel,command_delay=.1,
                             voltage_settling_time=.5,output_on_verify_delay=.05)
    if reader is None:
        reader = make_reader(args,supply,profile)

    rows = []
    connected_supply = False
    output_armed = False
    error = None
    fields=['trial','sequence','step','command_time_utc','elapsed_s',
            'command_duration_s','requested_ma','measured_ma',
            'supply_voltage_v','started','threshold_v']
    fields += [f"voltage_{m['board']}_{m['testpoint']}_v" for m in profile['mappings']]
    try:
        reader.connect()
        if hasattr(reader,'preflight'):
            reader.preflight()
        supply.connect()
        connected_supply = True
        metadata['supply_identity'] = getattr(supply,'identity','unknown')
        metadata['reader_identity'] = getattr(reader,'identity','unknown')
        metadata['status'] = 'running'
        with (out/'startup_steps.csv').open('w',newline='') as handle:
            writer = csv.DictWriter(handle,fieldnames=fields)
            writer.writeheader()
            for iteration in range(args.repeats):
                for label, steps in trial_plan:
                    # All trials start from a disabled HMP channel, not a
                    # programmed 0 A current limit (which HMP rejects).
                    if output_armed:
                        supply.output_off()
                        output_armed=False
                    sleep(args.off_time)
                    start = time.monotonic()
                    supply.prepare_scan(args.voltage_limit,steps[0].current_ma/1000)
                    output_armed = True
                    metadata['trials'].append(dict(repeat=iteration+1,mode=label))
                    for index, step in enumerate(steps):
                        begin = time.monotonic()
                        if index:
                            supply.set_current_limit(step.current_ma/1000)
                        command_duration = time.monotonic()-begin
                        sleep(step.dwell_s)
                        measured_a, measured_v = supply.readback()
                        if (not math.isfinite(measured_a) or not math.isfinite(measured_v)
                                or measured_a < 0
                                or measured_a > args.max_current_ma/1000 * 1.02
                                or measured_v < 0
                                or measured_v > args.voltage_limit * 1.03):
                            raise RuntimeError('Supply readback outside configured safety bounds')
                        readings = capture_step(reader,profile['mappings'])
                        vout = [value for name,value in readings.items()
                                if 'out' in name.lower()]
                        is_on = bool(vout) and all(value >= args.startup_threshold_v for value in vout)
                        row = dict(trial=iteration+1,sequence=label,step=index,
                                   command_time_utc=datetime.now(timezone.utc).isoformat(),
                                   elapsed_s=time.monotonic()-start,
                                   command_duration_s=command_duration,
                                   requested_ma=step.current_ma,
                                   measured_ma=measured_a*1000,
                                   supply_voltage_v=measured_v,
                                   started=is_on,threshold_v=args.startup_threshold_v)
                        for mapping in profile['mappings']:
                            name=mapping['testpoint']
                            row[f"voltage_{mapping['board']}_{name}_v"] = readings[name]
                        writer.writerow(row)
                        handle.flush()
                        rows.append(row)
                        metadata['completed_steps']+=1
                        print(f"trial {iteration+1} {label} {step.current_ma:g} mA: "
                              f"Vout={vout}, started={is_on}",flush=True)
                    supply.output_off()
                    output_armed=False
        metadata['status']='complete'
    except BaseException as exc:
        error=exc
        metadata['status']='interrupted' if isinstance(exc,(KeyboardInterrupt,InterruptedError)) else 'failed'
        metadata['error']=f'{type(exc).__name__}: {exc}'
    finally:
        if connected_supply:
            try:
                supply.output_off()
            except BaseException as exc:
                metadata['shutdown_error']=str(exc)
        try:
            reader.close()
        except BaseException as exc:
            metadata['reader_close_error']=str(exc)
        try:
            supply.close()
        except BaseException as exc:
            metadata['supply_close_error']=str(exc)
        (out/'metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
        if rows:
            try:
                plot_results(rows, profile, out)
            except BaseException as exc:
                metadata['plot_error'] = str(exc)
                (out/'metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    if error:
        raise error
    return out


def plot_results(rows, profile, out):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from collections import defaultdict
    trials=defaultdict(list)
    for row in rows:
        trials[(row['trial'],row['sequence'])].append(row)
    fig=Figure(figsize=(10,5),layout='constrained')
    FigureCanvasAgg(fig)
    ax=fig.subplots()
    for (repetition,label), grouped in trials.items():
        vout_name=next((f"voltage_{m['board']}_{m['testpoint']}_v"
                        for m in profile['mappings'] if 'out' in m['testpoint'].lower()),None)
        ax.plot([r['requested_ma'] for r in grouped],
                [r[vout_name] for r in grouped],
                marker='o',label=f'{label} repeat {repetition}')
    ax.set(xlabel='HMP programmed current limit (mA)',
           ylabel='Vout (V)',title='Shunt-LDO startup: step history comparison')
    ax.grid(alpha=.3);ax.legend()
    fig.savefig(out/'startup_comparison.png',dpi=200)
    fig.savefig(out/'startup_comparison.pdf')


def build_parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile',type=Path,help='Mapping profile JSON used by the GUI')
    p.add_argument('--simulate',action='store_true',help='No hardware energization')
    p.add_argument('--arm',action='store_true',help='Explicitly allow HMP output activation')
    p.add_argument('--port',default='/dev/hmp4040-1')
    p.add_argument('--channel',type=int,choices=range(1,5),default=2)
    p.add_argument('--baudrate',type=int,default=9600)
    p.add_argument('--voltage-limit',type=float,required=True)
    p.add_argument('--max-current-ma',type=float,default=100)
    p.add_argument('--direct-ma',type=float,default=25)
    p.add_argument('--target-ma',type=float,default=80)
    p.add_argument('--step-ma',type=float,default=5)
    p.add_argument('--dwell',type=float,default=1.0)
    p.add_argument('--off-time',type=float,default=2.0)
    p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--sequence',choices=('direct','staircase','both','recovery'),default='both')
    p.add_argument('--startup-threshold-v',type=float,default=1.0)
    p.add_argument('--resolution',type=int,choices=(8,10,12),default=12)
    p.add_argument('--capture-time',type=float,default=.02)
    p.add_argument('--sample-rate',type=float,default=1000000)
    p.add_argument('--output',type=Path,default=Path('results'))
    p.add_argument('--name',default='rd53_startup')
    return p


def main():
    args=build_parser().parse_args()
    profile=load_profile(args.profile) if args.profile else default_profile()
    destination=perform(args,profile)
    print('Saved startup test:',destination)


if __name__=='__main__':
    main()
