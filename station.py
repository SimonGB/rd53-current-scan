"""Mapped, multi-board measurements sharing one swept HMP channel."""
import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import threading
import time
import numpy as np
from mapping import validate_profile, load_profile
from scan import current_points, parser, SimSupply
from run_comments import RunComments
from board_setup import active_profile
from plot_style import signal_colors,sweep_style


def summaries(waves):
    if any(len(w)<2 or not np.isfinite(w).all() for w in waves):
        raise ValueError('Invalid or short measurement record')
    means=np.array([w.mean() for w in waves])
    pooled=np.concatenate(waves)
    return dict(mean=float(means.mean()),capture_std=float(means.std(ddof=1)) if len(means)>1 else None,
                sample_std=float(pooled.std(ddof=1)),
                within_capture_rms=float(np.sqrt(np.mean([np.var(w) for w in waves]))),
                peak_to_peak=float(np.ptp(pooled)),samples=len(pooled))


class SimMapped:
    identity='SIMULATED acquisition'
    sample_interval=1e-3
    def __init__(self,supply,mappings):
        self.supply=supply;self.mappings=mappings;self.rng=np.random.default_rng(53)
    def connect(self):pass
    def capture(self):
        gain=min(1,self.supply.current/.01)
        return {m['channel']:self.rng.normal((.88 if 'ref' in m['testpoint'].lower() else 1.8)*gain,
                                           .001,100) for m in self.mappings}
    def close(self):pass


class MappedPico:
    identity='PicoScope PS6000A'
    def __init__(self,args,mappings):
        from picoscope import PicoScope
        a,b=mappings
        self.driver=PicoScope(input_channel=a['channel'],output_channel=b['channel'],
            input_range=a['range'],output_range=b['range'],input_probe=a['probe'],output_probe=b['probe'],
            resolution_bits=args.resolution,sample_rate=args.sample_rate,capture_time=args.capture_time)
        self.channels=[a['channel'],b['channel']]
    def connect(self):self.driver.connect()
    @property
    def sample_interval(self):return self.driver.sample_interval
    def capture(self):return dict(zip(self.channels,self.driver.capture()))
    def close(self):self.driver.close()


def plot_mapped(rows,out):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    targets=list(dict.fromkeys((r['board'],r['testpoint'],r['unit']) for r in rows))
    colors=signal_colors(rows)
    fig=Figure(figsize=(9,max(4,2.8*len(targets))))
    FigureCanvasAgg(fig)
    for i,(board,tp,unit) in enumerate(targets):
        ax=fig.add_subplot(len(targets),1,i+1)
        for direction in ('up','down'):
            rr=[r for r in rows if (r['board'],r['testpoint'])==(board,tp) and r['direction']==direction]
            if rr:
                ax.errorbar([r['current_measured_A']*1000 for r in rr],[r['mean'] for r in rr],
                    yerr=[r['capture_std'] or 0 for r in rr],label=direction,**sweep_style(direction,colors[(board,tp,unit)]))
        ax.set_ylabel(f'{board} · {tp} [{unit}]');ax.grid(alpha=.25);ax.legend()
    ax.set_xlabel('Measured supply current [mA]')
    fig.tight_layout();fig.savefig(out/'mapped_voltages.png',dpi=150);fig.savefig(out/'mapped_voltages.pdf')


def run_mapped(args,profile,cancel=None,on_point=None,supply=None,reader=None,notes=None):
    profile=validate_profile(active_profile(profile)); cancel=cancel or threading.Event()
    notes=notes or RunComments(getattr(args,'comments',''))
    points=current_points(args.start,args.stop,args.step)
    plot_max=getattr(args,'plot_max_ma',100)
    if not math.isfinite(plot_max) or plot_max<=0:raise ValueError('Invalid plot maximum')
    for name in ('voltage_limit','max_current','sample_rate','capture_time'):
        if not math.isfinite(getattr(args,name)) or getattr(args,name)<=0:raise ValueError(f'Invalid {name}')
    if args.stop>args.max_current or args.max_current>10 or args.voltage_limit>32:
        raise ValueError('Sweep exceeds supply or configured current limits')
    if args.captures<1 or args.captures>10000 or not math.isfinite(args.settle) or args.settle<0:
        raise ValueError('Invalid capture count or settling delay')
    if args.sample_rate*args.capture_time*args.captures>20000000:
        raise ValueError('At most 20 million nominal samples per point')
    if not args.name or args.name in ('.','..') or '/' in args.name or '\\' in args.name:
        raise ValueError('Run name must be a single directory component')
    if cancel.is_set():raise InterruptedError('Stop requested')
    mappings=profile['mappings']; units={m['channel']:'V' for m in mappings}
    if supply is None:
        if args.simulate:supply=SimSupply()
        else:
            from instruments import HMP4040
            supply=HMP4040(port=args.port,baudrate=args.baudrate,channel=args.channel,
                command_delay=.1,voltage_settling_time=.5,output_on_verify_delay=.05)
    if reader is None:
        if args.simulate:reader=SimMapped(supply,mappings)
        elif profile['backend']=='pico':reader=MappedPico(args,mappings)
        else:
            from venelin import Venelin
            adc=profile.get('adc',{})
            reader=Venelin(adc['root'],adc['config'],adc['data'],[m['channel'] for m in mappings],
                           args.sample_rate,args.capture_time,cancel)
            units={m['channel']:('A' if reader.profiles[m['channel']]['kind']=='I' else 'V') for m in mappings}
    out=args.output/args.name/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ');out.mkdir(parents=True)
    metadata={'settings':{k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
              'comments':getattr(args,'comments',''),'profile':profile,'simulation':args.simulate,'status':'initializing','completed_points':0}
    notes.bind(out)
    rows=[];armed=False;failure=None;errors=[]
    processing=ThreadPoolExecutor(max_workers=2,thread_name_prefix='rd53-data')
    pending_raw=None
    metadata['acquisition_mode']='batched_contiguous_windows' if hasattr(reader,'capture_many') else 'separate_captures'
    metadata['point_timings_s']=[]
    fields=['point','direction','timestamp_UTC','board','testpoint','input','unit','current_set_A',
            'current_measured_A','supply_voltage_V','current_tracking_ok','mean','capture_std',
            'sample_std','within_capture_rms','peak_to_peak','samples','comments','comments_revision']
    try:
        reader.connect()
        # Test ADC acquisition before touching the DUT supply. Not a zero calibration.
        if hasattr(reader,'preflight'):reader.preflight()
        supply.connect()
        metadata['supply_identity']=supply.identity;metadata['reader_identity']=reader.identity
        if hasattr(reader,'profiles'):metadata['adc_input_profiles']=reader.profiles
        if hasattr(reader,'chip_mask'):metadata['adc_chip_mask']=hex(reader.chip_mask)
        if hasattr(reader,'drdy_chip'):metadata['adc_drdy_chip']=reader.drdy_chip
        if hasattr(reader,'driver'):metadata['scope_resolution_bits']=reader.driver.resolution_bits
        if cancel.is_set():raise InterruptedError('Stop requested')
        armed=True;supply.prepare_scan(args.voltage_limit,points[0])
        seq=[('up',x) for x in points]+([('down',x) for x in reversed(points)] if args.reverse else [])
        with (out/'measurements.csv').open('w',newline='') as f:
            writer=csv.DictWriter(f,fields);writer.writeheader()
            for index,(direction,current) in enumerate(seq):
                point_started=time.monotonic()
                if cancel.is_set():raise InterruptedError('Stop requested')
                supply.set_current_limit(current)
                if cancel.wait(.02 if args.simulate else args.settle):raise InterruptedError('Stop requested')
                if pending_raw is not None:pending_raw.result();pending_raw=None
                before=supply.number('MEAS:CURR?') if hasattr(supply,'number') else supply.readback()[0]
                if not math.isfinite(before) or before>args.max_current+max(.0001,.02*args.max_current):
                    raise RuntimeError('Invalid or excessive supply current')
                capture_started=time.monotonic()
                if hasattr(reader,'capture_many'):
                    captures=reader.capture_many(args.captures)
                else:
                    captures=[]
                    for _ in range(args.captures):
                        if cancel.is_set():raise InterruptedError('Stop requested')
                        captures.append(reader.capture())
                capture_seconds=time.monotonic()-capture_started
                after,voltage=supply.readback()
                if not all(math.isfinite(v) for v in (after,voltage)) or after>args.max_current+max(.0001,.02*args.max_current):
                    raise RuntimeError('Invalid or excessive supply readback')
                measured=(before+after)/2; timestamp=datetime.now(timezone.utc).isoformat();batch=[]
                comment_text,comment_revision=notes.snapshot()
                futures={m['channel']:processing.submit(summaries,[np.asarray(c[m['channel']],dtype=float) for c in captures]) for m in mappings}
                for m in mappings:
                    stats=futures[m['channel']].result()
                    row=dict(point=index,direction=direction,timestamp_UTC=timestamp,board=m['board'],
                        testpoint=m['testpoint'],input=m['channel'],unit=units[m['channel']],current_set_A=current,
                        current_measured_A=measured,supply_voltage_V=voltage,
                        current_tracking_ok=abs(measured-current)<=max(.0001,.02*current),comments=comment_text,comments_revision=comment_revision,**stats)
                    batch.append(row)
                writer.writerows(batch);f.flush();rows.extend(batch)
                if args.save_waveforms:
                    arrays={f'{c}_capture{j}':v for j,cap in enumerate(captures) for c,v in cap.items()}
                    pending_raw=processing.submit(np.savez_compressed,out/f'waveforms_{index:05d}.npz',sample_interval_s=reader.sample_interval,**arrays)
                metadata['point_timings_s'].append(dict(point=index,acquisition=capture_seconds,total=time.monotonic()-point_started))
                metadata['completed_points']+=1
                notes.point_completed(index)
                if on_point:on_point(batch)
                print(f'{direction}: {measured*1000:.3f} mA · {len(batch)} mapped inputs',flush=True)
        metadata['status']='complete'
    except BaseException as exc:
        failure=exc;metadata['status']='interrupted' if isinstance(exc,(InterruptedError,KeyboardInterrupt)) else 'failed'
        metadata['error']=f'{type(exc).__name__}: {exc}'
    finally:
        if armed:
            try:supply.output_off()
            except BaseException as e:errors.append(f'OUTPUT OFF FAILED: {e}')
        for device in (supply,reader):
            try:device.close()
            except BaseException as e:errors.append(f'Close failed: {e}')
        try:
            if pending_raw is not None:pending_raw.result()
        except BaseException as e:
            metadata['raw_save_error']=str(e)
            if failure is None:failure=e;metadata['status']='failed'
        finally:processing.shutdown(wait=True)
        if errors:metadata['status']='shutdown_failed'
        metadata['shutdown_errors']=errors
        if rows:
            try:
                plot_mapped(rows,out)
                from sweep_plots import export_sweeps
                export_sweeps(rows,out,getattr(args,'plot_title','5 RD53b in 3 serial domains'),
                              getattr(args,'plot_max_ma',100),
                              configuration=getattr(args,'plot_configuration',''))
            except Exception as e:metadata['plot_error']=str(e)
        notes.finalize(metadata)
        print(f'Results: {out}',flush=True)
    if errors:raise RuntimeError('; '.join(errors)) from failure
    if failure:raise failure
    return out


if __name__=='__main__':
    import sys
    import signal
    p=parser()
    a=p.parse_args()
    if a.profile is None:p.error('--profile is required for station.py')
    stop=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set())
    try:run_mapped(a,load_profile(a.profile),stop)
    except (InterruptedError,KeyboardInterrupt):sys.exit(130)
    except Exception as e:print(f'ERROR: {e}',file=sys.stderr);sys.exit(1)



