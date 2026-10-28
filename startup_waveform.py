"""Triggered four-channel startup sweep; all hardware access is injectable."""
from __future__ import annotations
import csv
import json
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from board_setup import active_profile
from mapping import validate_profile


def parse_currents(text):
    try: values=[float(x.strip()) for x in text.split(',') if x.strip()]
    except ValueError as e: raise ValueError('Enter comma-separated currents in mA') from e
    if not values or len(values)>100 or any(not math.isfinite(v) or v<=0 for v in values):
        raise ValueError('Enter 1–100 positive, finite current settings')
    return values


def voltage_values(settings):
    # Legacy single-voltage runs remain usable.
    return settings.get('voltage_limits_v', [settings['voltage_limit']] if 'voltage_limit' in settings else [])


def parse_voltages(text):
    try: values=[float(x.strip()) for x in text.split(',') if x.strip()]
    except ValueError as e: raise ValueError('Enter comma-separated voltage limits in V') from e
    if not values or len(values)>100 or any(not math.isfinite(v) or v<=0 for v in values):
        raise ValueError('Enter 1–100 positive, finite voltage limits')
    return values


def validate(settings,profile):
    p=validate_profile(active_profile(profile))
    if p['backend']!='pico': raise ValueError('Startup waveform sweep requires PicoScope')
    mappings=p['mappings'];channels=[m['channel'] for m in mappings]
    if settings['trigger_channel'] not in channels: raise ValueError('Trigger channel must be mapped')
    for key in ('pre_ms','post_ms','sample_rate','timeout_s','off_s','max_current_ma'):
        v=settings[key]
        if not math.isfinite(v) or v<=0: raise ValueError(f'{key} must be positive and finite')
    if not math.isfinite(settings['trigger_v']): raise ValueError('Trigger voltage must be finite')
    volts=voltage_values(settings)
    if not volts or len(volts)>100 or any(not math.isfinite(v) or v<=0 for v in volts):raise ValueError('Voltage limits must be positive and finite')
    voltage_ceiling=settings.get('max_voltage_v', settings.get('voltage_limit',0))
    if not math.isfinite(voltage_ceiling) or not 0<voltage_ceiling<=32 or max(volts)>voltage_ceiling:raise ValueError('A voltage setting exceeds the verified voltage ceiling')
    if settings['max_current_ma']>10000: raise ValueError('Limit exceeds HMP range')
    if not settings['currents_ma'] or any(not math.isfinite(v) or v<=0 for v in settings['currents_ma']):raise ValueError('Current settings must be positive and finite')
    if len(volts)*len(settings['currents_ma'])*settings['repeats']>10000:raise ValueError('Sweep exceeds 10000 captures')
    if max(settings['currents_ma'])>settings['max_current_ma']: raise ValueError('A current setting exceeds the configured limit')
    if settings['pre_ms']>1000 or settings['post_ms']>1000 or settings['timeout_s']>30 or settings['off_s']>300: raise ValueError('Capture or delay exceeds supported range')
    if (settings['pre_ms']+settings['post_ms'])*settings['sample_rate']/1000>2_000_000: raise ValueError('Capture exceeds two million samples per channel')
    if not 1<=settings['repeats']<=100: raise ValueError('Repeats must be 1–100')
    if settings['channel'] not in (1,2,3,4): raise ValueError('Invalid Hameg channel')
    m=next(x for x in mappings if x['channel']==settings['trigger_channel'])
    if abs(settings['trigger_v']) >= float(m['range'])*float(m['probe']): raise ValueError('Trigger voltage must be within trigger channel range at the probe tip')
    name=settings['name']
    if not name or name in ('.','..') or '/' in name or '\\' in name:raise ValueError('Invalid result name')
    return p


class HamegStartup:
    """Preconfigure one output off; do not modify any other channel."""
    def __init__(self,s):
        from instruments import HMP4040
        self.device=HMP4040(port=s['port'],channel=s['channel'],command_delay=.05,voltage_settling_time=.25)
        self.master_on=None;self.ready=False
    def connect(self):
        d=self.device;d.connect();self.master_on=bool(int(d.query('OUTP:GEN?')))
        if not self.master_on:
            for ch in range(1,5):
                if ch==d.channel:continue
                d.write(f'INST OUT{ch}')
                if int(d.query('OUTP:SEL?')):
                    d.select_channel(d.channel)
                    raise RuntimeError(f'CH{ch} selected while master is off; deselect before starting')
        d.select_channel(d.channel)
        # Refuse to interrupt an already powered DUT unexpectedly.
        if self.master_on and int(d.query('OUTP:SEL?')):raise RuntimeError('Switch the DUT channel off before starting')
        self.ready=True
    def prepare(self,current_a,voltage):
        d=self.device;d.select_channel(d.channel);d.write('OUTP:SEL OFF')
        d.set_current_limit(current_a);d.set_voltage(voltage)
        d._verify_voltage_setting(voltage)
        if int(d.query('OUTP:SEL?')):raise RuntimeError('DUT channel did not turn off')
    def on(self):
        d=self.device;d.select_channel(d.channel);d.write('OUTP:SEL ON')
        if not self.master_on:d.write('OUTP:GEN ON')
    def off(self):
        if not self.ready:return
        d=self.device;d.select_channel(d.channel);d.write('OUTP:SEL OFF')
        if not self.master_on:d.write('OUTP:GEN OFF')
    def close(self):self.device.close()


class TriggeredPico:
    def __init__(self,settings,profile):
        from picoscope import PicoScope
        channels={m['channel']:{'range':m['range'],'probe':m['probe']} for m in profile['mappings']}
        self.scope=PicoScope(channels=channels,resolution_bits=12,sample_rate=settings['sample_rate'],capture_time=(settings['pre_ms']+settings['post_ms'])/1000)
        self.settings=settings
    def connect(self):self.scope.connect()
    def capture(self,action,cancel):
        from ctypes import byref,c_double,c_int16,c_int32,c_uint32,c_uint64,c_void_p
        from picosdk.ps6000a import ps6000a as sdk
        s=self.settings;scope=self.scope; dt=scope.sample_interval
        pre=max(1,round(s['pre_ms']/1000/dt));post=max(1,round(s['post_ms']/1000/dt));count=pre+post
        scope._validate_sample_count(count);dt=scope.sample_interval
        buffers={ch:np.zeros(count,dtype=np.int16) for ch in scope.channel_settings}
        for index,(ch,cfg) in enumerate(scope.channel_settings.items()):
            status=sdk.ps6000aSetDataBuffer(scope.handle,cfg['channel'],buffers[ch].ctypes.data_as(c_void_p),count,scope.data_type,0,scope.ratio_mode,(scope.clear_all|scope.add) if index==0 else scope.add)
            scope.check(status,f'{ch} buffer')
        cfg=scope.channel_settings[s['trigger_channel']]
        threshold=int(round(s['trigger_v']/(cfg['range']*cfg['probe'])*scope.adc_max))
        if not hasattr(sdk,'ps6000aGetScopeState'):
            try:sdk.make_symbol('_GetScopeState','ps6000aGetScopeState',c_uint32,[c_int16,c_void_p])
            except (AttributeError, OSError) as e:raise RuntimeError('PicoSDK driver lacks GetScopeState; update the ps6000a driver before this sweep') from e
        scope.check(sdk.ps6000aSetSimpleTrigger(scope.handle,1,cfg['channel'],threshold,2,0,0),'SetSimpleTrigger')
        indisposed=c_double()
        scope.check(sdk.ps6000aRunBlock(scope.handle,pre,post,scope.timebase,byref(indisposed),0,None,None),'RunBlock')
        try:
            # WAITING_FOR_TRIGGER (2) confirms pretrigger is full; fail closed if triggered early.
            deadline=time.monotonic()+s['timeout_s'];state=c_int32()
            while True:
                if cancel.is_set():raise InterruptedError('Stopped before power-on')
                scope.check(sdk.ps6000aGetScopeState(scope.handle,byref(state)),'GetScopeState')
                if state.value==2:break
                if state.value in (3,4):raise RuntimeError('Unexpected trigger before supply was switched on')
                if time.monotonic()>deadline:raise TimeoutError('Scope did not arm with full pre-trigger buffer')
                time.sleep(.002)
            action()
            deadline=time.monotonic()+s['timeout_s'];ready=c_int16()
            while not ready.value:
                if cancel.is_set():raise InterruptedError('Stopped during trigger wait')
                scope.check(sdk.ps6000aIsReady(scope.handle,byref(ready)),'IsReady')
                if time.monotonic()>deadline:raise TimeoutError('No trigger; check threshold and wiring')
                time.sleep(.002)
            n=c_uint64(count);overflow=c_int16()
            scope.check(sdk.ps6000aGetValues(scope.handle,0,byref(n),1,scope.ratio_mode,0,byref(overflow)),'GetValues')
            if n.value!=count:raise RuntimeError(f'Incomplete capture: {n.value}/{count} samples')
            over=[ch for ch,c in scope.channel_settings.items() if overflow.value & (1<<c['channel'])]
            if over:raise RuntimeError('PicoScope over-range: '+', '.join(over))
            values={ch:buf.astype(np.float64)/scope.adc_max*scope.channel_settings[ch]['range']*scope.channel_settings[ch]['probe'] for ch,buf in buffers.items()}
            return (np.arange(count)-pre)*dt,values,{'sample_interval_s':dt,'pre_samples':pre,'post_samples':post,'resolution_bits':scope.resolution_bits,'trigger_adc':threshold}
        finally:
            sdk.ps6000aStop(scope.handle)
            # Do not leave a trigger configured if the device is reused.
            scope.check(sdk.ps6000aSetSimpleTrigger(scope.handle,0,cfg['channel'],0,2,0,0),'Disable trigger')
    def close(self):self.scope.close()


def draw_overlay(records,profile,destination=None,figure=None):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    mappings=profile['mappings']
    fig=figure or Figure(figsize=(10,max(3,2.3*len(mappings))))
    if figure is None:FigureCanvasAgg(fig)
    fig.clear();axes=fig.subplots(len(mappings),1,squeeze=False,sharex=True)[:,0]
    combos=list(dict.fromkeys((r['current_ma'],r.get('voltage_limit_v')) for r in records))
    from matplotlib import colormaps
    colors={c:colormaps['turbo'](i/max(1,len(combos)-1)) for i,c in enumerate(combos)}
    for ax,m in zip(axes,mappings):
        for r in records:
            if m['channel'] not in r['values']:continue
            v=r.get('voltage_limit_v'); vlabel=f'{v:g} V' if v is not None else 'V limit unknown'
            ax.plot(r['time_s']*1000,r['values'][m['channel']],color=colors[(r['current_ma'],v)],alpha=.65,linewidth=.9,label=f"{r['current_ma']:g} mA · {vlabel} · #{r['repeat']}")
        ax.axvline(0,color='black',linewidth=.6,alpha=.5);ax.set_ylabel(f"{m['board']} / {m['testpoint']} [V]");ax.grid(alpha=.25)
    axes[-1].set_xlabel('Time from trigger [ms]')
    if records:axes[0].legend(fontsize=7,ncol=2)
    fig.tight_layout()
    if destination:
        fig.savefig(destination/'startup_overlay.png',dpi=150);fig.savefig(destination/'startup_overlay.pdf')
    return fig


class SimSupply:
    def connect(self):pass
    def prepare(self,current_a,voltage):self.current=current_a
    def on(self):pass
    def off(self):pass
    def close(self):pass

class SimScope:
    def __init__(self,s,profile):self.s=s;self.profile=profile
    def connect(self):pass
    def capture(self,action,cancel):
        if cancel.is_set():raise InterruptedError('Stopped')
        action();dt=1/self.s['sample_rate'];pre=round(self.s['pre_ms']/1000/dt);post=round(self.s['post_ms']/1000/dt)
        t=(np.arange(pre+post)-pre)*dt
        ramp=np.where(t>=0,1-np.exp(-np.maximum(t,0)/.0004),0)
        values={m['channel']:ramp*(.6 if 'ref' in m['testpoint'].lower() else 1.2) for m in self.profile['mappings']}
        return t,values,{'sample_interval_s':dt,'pre_samples':pre,'post_samples':post,'resolution_bits':'simulated'}
    def close(self):pass

def run(settings,profile,cancel=None,on_capture=None,supply=None,scope=None):
    p=validate(settings,profile);cancel=cancel or threading.Event()
    out=Path(settings['output'])/settings['name']/('waveform_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'));out.mkdir(parents=True)
    meta={'settings':{k:str(v) if isinstance(v,Path) else v for k,v in settings.items()},'profile':p,'status':'initializing','captures':[],'time_reference':'trigger threshold crossing; sample quantized'}
    records=[];connected=False;error=None;cleanup=[]
    try:
        if supply is None:supply=SimSupply() if settings.get('simulate') else HamegStartup(settings)
        if scope is None:scope=SimScope(settings,p) if settings.get('simulate') else TriggeredPico(settings,p)
        scope.connect();supply.connect();connected=True;meta['status']='running'
        volts=voltage_values(settings)
        total=len(settings['currents_ma'])*len(volts)*settings['repeats']
        for voltage in volts:
            for current in settings['currents_ma']:
                for repeat in range(1,settings['repeats']+1):
                    if cancel.wait(settings['off_s']):raise InterruptedError('Stopped')
                    supply.prepare(current/1000,voltage)
                    try:
                        t,values,details=scope.capture(supply.on,cancel)
                    finally:supply.off()
                    stem=f'{len(records)+1:04d}_voltage_{voltage:g}V_current_{current:g}mA_repeat_{repeat:03d}'
                    np.savez_compressed(out/(stem+'.npz'),time_s=t,**values)
                    with (out/(stem+'.csv')).open('w',newline='') as f:
                        w=csv.writer(f);w.writerow(['time_s']+[m['channel'] for m in p['mappings']]);w.writerows(zip(t,*[values[m['channel']] for m in p['mappings']]))
                    entry={'current_ma':current,'voltage_limit_v':voltage,'repeat':repeat,'file':stem+'.npz','details':details};meta['captures'].append(entry)
                    record={'current_ma':current,'voltage_limit_v':voltage,'repeat':repeat,'time_s':t,'values':values};records.append(record)
                    (out/'metadata.json').write_text(json.dumps(meta,indent=2))
                    if on_capture:on_capture(record,total)
        meta['status']='complete'
    except BaseException as exc:
        error=exc;meta['status']='interrupted' if isinstance(exc,InterruptedError) else 'failed';meta['error']=f'{type(exc).__name__}: {exc}'
    finally:
        if connected:
            try:supply.off()
            except BaseException as e:cleanup.append(f'Output off failed: {e}')
        for device in (scope,supply):
            if device:
                try:device.close()
                except BaseException as e:cleanup.append(f'Close failed: {e}')
        meta['cleanup_errors']=cleanup
        if records:
            try:draw_overlay(records,p,out)
            except BaseException as e:meta['plot_error']=str(e)
        (out/'metadata.json').write_text(json.dumps(meta,indent=2))
    if cleanup:raise RuntimeError(f"{error or 'Run completed'}; {'; '.join(cleanup)}; results: {out}")
    if error:raise RuntimeError(f'{error}; partial results: {out}') from error
    return out
