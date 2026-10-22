"""Remote DG4162 frequency-step sweep; generator-only, not an SLDO current source.

Requires a deliberate --enable-output and --confirm-safe-interface to emit
a waveform. A dry run or --identify does NOT enable the output.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

import numpy as np


def frequency_points(start_hz, stop_hz, count, spacing):
    if not all(math.isfinite(float(x)) for x in (start_hz, stop_hz)):
        raise ValueError('Frequencies must be finite')
    if not (0 < start_hz < stop_hz <= 50e6):
        raise ValueError('Require 0 < start < stop <= 50 MHz (DG4162 square wave)')
    if not (2 <= count <= 1000):
        raise ValueError('Count must be between 2 and 1000')
    if spacing == 'log':
        return np.geomspace(start_hz, stop_hz, count).tolist()
    if spacing == 'linear':
        return np.linspace(start_hz, stop_hz, count).tolist()
    raise ValueError('Spacing must be linear or log')


class DG4162:
    """Small VISA transport wrapper with conservative remote commands."""
    def __init__(self, resource, channel=1, manager=None):
        if channel not in (1, 2):
            raise ValueError('DG4162 channel must be 1 or 2')
        self.resource=resource
        self.channel=channel
        self.manager=manager
        self.device=None
        self.owns_manager=manager is None

    def connect(self):
        if self.manager is None:
            import pyvisa
            self.manager=pyvisa.ResourceManager()
        self.device=self.manager.open_resource(self.resource)
        self.device.timeout=3000
        self.device.write_termination='\n'
        self.device.read_termination='\n'
        identity=self.device.query('*IDN?').strip()
        if 'DG4162' not in identity.upper():
            raise RuntimeError(f'Wrong instrument: {identity}')
        return identity

    def write(self, command):
        if self.device is None:
            raise RuntimeError('DG4162 not connected')
        self.device.write(command)

    def query(self, command):
        if self.device is None:
            raise RuntimeError('DG4162 not connected')
        return self.device.query(command).strip()

    def output_off(self):
        self.write(f':OUTP{self.channel} OFF')

    def output_on(self):
        self.write(f':OUTP{self.channel} ON')

    def configure(self, wave, amplitude_vpp, offset_v, first_frequency_hz):
        ch=self.channel
        self.output_off()
        # Fix load setting explicitly: High-Z calibrates amplitude at a
        # high-impedance measuring input, NOT a high-impedance physical output.
        self.write(f':OUTP{ch}:LOAD INF')
        self.write(f':SOUR{ch}:SWE:STAT OFF')
        self.write(f':SOUR{ch}:BURS:STAT OFF')
        self.write(f':SOUR{ch}:MOD:STAT OFF')
        self.write(f':SOUR{ch}:FUNC {"SQU" if wave=="square" else "SIN"}')
        self.write(f':SOUR{ch}:VOLT {amplitude_vpp:.9g}')
        self.write(f':SOUR{ch}:VOLT:OFFS {offset_v:.9g}')
        self.set_frequency(first_frequency_hz)

    def set_frequency(self, frequency_hz):
        self.write(f':SOUR{self.channel}:FREQ {frequency_hz:.12g}')

    def frequency_readback(self):
        return float(self.query(f':SOUR{self.channel}:FREQ?'))

    def close(self):
        try:
            if self.device is not None:
                self.device.close()
        finally:
            if self.owns_manager and self.manager is not None:
                self.manager.close()


def make_parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--resource', help='USB VISA descriptor or LAN TCPIP resource')
    p.add_argument('--identify',action='store_true',help='Identify generator only; do not change output')
    p.add_argument('--list-resources',action='store_true',help='Show VISA resources; do not energize anything')
    p.add_argument('--simulate',action='store_true',help='Preview frequencies without hardware')
    p.add_argument('--enable-output',action='store_true',
                   help='Explicitly enable waveform output after checking physical interface')
    p.add_argument('--confirm-safe-interface',action='store_true',
                   help='Confirm an appropriate current-limited and isolated interface is wired')
    p.add_argument('--channel',type=int,choices=(1,2),default=1)
    p.add_argument('--wave',choices=('square','sine'),default='square')
    p.add_argument('--start-hz',type=float,default=1)
    p.add_argument('--stop-hz',type=float,default=10000)
    p.add_argument('--points',type=int,default=25)
    p.add_argument('--spacing',choices=('log','linear'),default='log')
    p.add_argument('--amplitude-vpp',type=float,default=None,
                   help='Explicit High-Z calibrated Vpp (required for output)')
    p.add_argument('--offset-v',type=float,default=0)
    p.add_argument('--cycles',type=float,default=20,
                   help='Minimum number of full cycles at each step')
    p.add_argument('--min-dwell-s',type=float,default=.2,
                   help='Minimum frequency-step hold time (s)')
    p.add_argument('--max-dwell-s',type=float,default=30)
    p.add_argument('--output',type=Path,default=Path('results/dg4162_frequency_sweep'))
    return p


def validate(args):
    if args.simulate and (args.enable_output or args.identify or args.list_resources):
        raise ValueError('Simulation cannot access instruments')
    if args.enable_output and not args.confirm_safe_interface:
        raise ValueError('Output disabled: require --confirm-safe-interface')
    if args.enable_output and not args.resource:
        raise ValueError('Provide explicit --resource before enabling output')
    if args.enable_output and args.amplitude_vpp is None:
        raise ValueError('Explicit --amplitude-vpp required for enabled output')
    if not args.simulate and not args.resource and not args.list_resources:
        raise ValueError('Specify --resource, or use --simulate / --list-resources')
    for value in (args.offset_v,args.cycles,args.min_dwell_s,args.max_dwell_s):
        if not math.isfinite(value):
            raise ValueError('Sweep settings must be finite')
    if args.amplitude_vpp is not None:
        if not math.isfinite(args.amplitude_vpp) or not (0 < args.amplitude_vpp <= 2):
            raise ValueError('Output amplitude must be in (0, 2] Vpp; not a DUT safety guarantee')
        if abs(args.offset_v) + args.amplitude_vpp/2 > 2:
            raise ValueError('High/low waveform excursions exceed ±2 V guardrail')
    if not (args.cycles >= 1 and 0 < args.min_dwell_s <= args.max_dwell_s <= 120):
        raise ValueError('Invalid cycles/dwell limits')
    if not (args.list_resources or args.identify):
        frequency_points(args.start_hz,args.stop_hz,args.points,args.spacing)


def run(args, manager=None, clock=time.monotonic, sleeper=time.sleep):
    validate(args)
    if args.list_resources:
        if manager is None:
            import pyvisa
            manager=pyvisa.ResourceManager()
            try:
                return list(manager.list_resources())
            finally:
                manager.close()
        return list(manager.list_resources())
    if args.simulate:
        return [(f,max(args.min_dwell_s,min(args.max_dwell_s,args.cycles/f)))
                for f in frequency_points(args.start_hz,args.stop_hz,args.points,args.spacing)]
    device=DG4162(args.resource,args.channel,manager)
    connected=False
    active=False
    try:
        identity=device.connect()
        connected=True
        if args.identify:
            return identity
        frequencies=frequency_points(args.start_hz,args.stop_hz,args.points,args.spacing)
        if not args.enable_output:
            # No configuration changes or waveform output until armed.
            return dict(identity=identity,preview=frequencies,
                        message='No output emitted; add --enable-output and --confirm-safe-interface')
        metadata=dict(identity=identity,settings={k:str(v) if isinstance(v,Path) else v
                       for k,v in vars(args).items()})
        args.output.mkdir(parents=True,exist_ok=True)
        path=args.output/('dg4162_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
        path.mkdir(exist_ok=False)
        (path/'metadata.json').write_text(json.dumps(metadata,indent=2))
        results=[]
        device.configure(args.wave,args.amplitude_vpp,args.offset_v,frequencies[0])
        start=clock()
        with (path/'frequency_steps.csv').open('w',newline='') as output:
            writer=csv.DictWriter(output,fieldnames=['index','requested_hz',
                       'readback_hz','dwell_s','elapsed_s','time_utc'])
            writer.writeheader()
            # Mark activation attempt first: if the USB write raises after
            # enabling the physical output, finally still sends OFF.
            active=True
            device.output_on()
            for index, frequency in enumerate(frequencies):
                device.set_frequency(frequency)
                actual=device.frequency_readback()
                if not math.isfinite(actual) or abs(actual-frequency)>max(.01,frequency*.001):
                    raise RuntimeError(f'Frequency readback mismatch: requested {frequency}, got {actual} Hz')
                dwell=max(args.min_dwell_s,min(args.max_dwell_s,args.cycles/frequency))
                sleeper(dwell)
                row=dict(index=index,requested_hz=frequency,readback_hz=actual,
                         dwell_s=dwell,elapsed_s=clock()-start,
                         time_utc=datetime.now(timezone.utc).isoformat())
                writer.writerow(row);output.flush();results.append(row)
                print(f"{index+1}/{len(frequencies)}: {frequency:g} Hz, dwell {dwell:.3f} s",flush=True)
        return path
    finally:
        # Never leave the output running after the automated sweep.
        try:
            if connected and active:
                device.output_off()
        finally:
            device.close()


def main():
    args=make_parser().parse_args()
    outcome=run(args)
    if args.list_resources:
        if outcome:
            print('VISA resources:')
            for resource in outcome:
                print(' ', resource)
        else:
            print('No VISA resources found.')
    elif args.simulate:
        for frequency, dwell in outcome:
            print(f'{frequency:.6g} Hz: dwell {dwell:.3f} s')
    else:
        print(outcome)


if __name__=='__main__':
    main()
