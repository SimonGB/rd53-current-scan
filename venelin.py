"""Finite acquisitions through SimonGB/pmod_adc's existing compiled helpers."""
import math
from pathlib import Path
import re
import signal
import subprocess
import time
import numpy as np
try:
    import tomllib
except ImportError:
    import tomli as tomllib


def input_profiles(path):
    with Path(path).open('rb') as f: config=tomllib.load(f)
    cal=config.get('Calibration',{})
    bits=int(cal.get('PrecisionBits',24))
    if bits not in (16,24): raise ValueError('PrecisionBits must be 16 or 24')
    profiles={}
    legacy=['dV','dV','dV','I','I','I','aV','aV']
    for p in range(1,7):
        table=config.get('BoardInputs',{}).get(f'PMOD{p}',{})
        kinds=table.get('Kinds',legacy)
        defaults={'dV':cal.get('DiffVoltRef',3.6),'I':cal.get('DiffCurrRef',.012),'aV':cal.get('AbsVoltRef',64)}
        spans=table.get('FullScaleVolts',[defaults[k] for k in kinds])
        shunts=table.get('ShuntOhms',[cal.get('ShuntRef',.005)]*8)
        if any(len(a)!=8 for a in (kinds,spans,shunts)):
            raise ValueError('PMOD profile arrays require eight entries')
        if any(k not in ('dV','I') for k in kinds[:6]) or list(kinds[6:])!=['aV','aV']:
            raise ValueError('Ch0..5 must be dV/I; Ch6..7 must be aV')
        for ch,(kind,span,shunt) in enumerate(zip(kinds,spans,shunts)):
            if not all(math.isfinite(float(x)) and float(x)>0 for x in (span,shunt)):
                raise ValueError('ADC spans/shunts must be positive and finite')
            profiles[f'PMOD{p}_Ch{ch}']={'kind':kind,'full_scale_volts':float(span),
                                        'shunt_ohms':float(shunt),'bits':bits}
    return profiles


def decode_file(path, channels, profiles):
    """Use header physical IDs, never assume stored-column == global channel."""
    identities=None; values={c:[] for c in channels}; ticks=[]; pending=''
    for raw in Path(path).read_text().splitlines():
        line=raw.strip()
        if not line: continue
        if line.startswith('#'):
            found=re.findall(r'PMOD([1-6])_(?:dV|I|aV|Ch)([0-7])\b',line,re.I)
            if found:
                new=[f'PMOD{p}_Ch{c}' for p,c in found]
                if identities is not None and new!=identities:
                    raise ValueError('ADC header changed within capture')
                if len(new)!=len(set(new)): raise ValueError('Duplicate ADC header IDs')
                identities=new
            continue
        if line.endswith('-'):
            pending+=line[:-1]; continue
        line=pending+line; pending=''
        fields=line.split()
        if identities is None: raise ValueError('ADC data missing physical-channel header')
        if any(c not in identities for c in channels): raise ValueError('Requested channel absent from ADC header')
        if len(fields)!=len(identities)+2: raise ValueError('Malformed ADC data row')
        try:
            int(fields[0],0); tick=int(fields[1],0)
            codes=[int(x) for x in fields[2:]]
        except ValueError as e: raise ValueError('Invalid ADC row') from e
        ticks.append(tick)
        for c in channels:
            profile=profiles[c]; denom=1<<(profile['bits']-1)
            code=codes[identities.index(c)]
            if not -denom < code < denom-1:
                raise ValueError(f'{c}: ADC saturated or code outside signed range')
            scale=profile['full_scale_volts']/denom
            if profile['kind']=='I': scale/=profile['shunt_ohms']
            values[c].append(code*scale)
    if pending or len(ticks)<2: raise ValueError('Incomplete or empty ADC capture')
    return {c:np.asarray(v) for c,v in values.items()},np.asarray(ticks,dtype=np.int64)


class Venelin:
    def __init__(self, root, config, data, channels, sample_rate, capture_time, cancel):
        self.root=Path(root).expanduser().resolve()
        self.profiles=input_profiles(config)
        self.data=Path(data).expanduser().resolve()
        self.channels=channels
        self.rate=int(sample_rate)
        if sample_rate!=self.rate or self.rate not in (250,500,1000,2000,4000,8000,16000,32000):
            raise ValueError('Choose an ADC rate: 250, 500, 1000, 2000, 4000, 8000, 16000, 32000 S/s')
        self.count=max(2,math.ceil(self.rate*capture_time))
        self.cancel=cancel; self.process=None
        self.sample_interval=1/self.rate
        self.identity='Venelin PMOD ADC via pm_adcs helpers'
        self.last_ticks=None

    def command(self, name, args, timeout):
        exe=self.root/'install/bin'/name
        if not exe.is_file(): raise FileNotFoundError(f'Build pmod_adc first: missing {exe}')
        # File-backed logs avoid pipe deadlocks on verbose acquisition output.
        import tempfile
        with tempfile.TemporaryFile(mode='w+') as log:
            self.process=subprocess.Popen([str(exe),*args],cwd=self.root,stdout=log,stderr=log)
            deadline=time.monotonic()+timeout
            try:
                while self.process.poll() is None:
                    if self.cancel.is_set(): raise InterruptedError('Stop requested')
                    if time.monotonic()>deadline: raise TimeoutError(f'{name} timed out')
                    time.sleep(.02)
                if self.process.returncode:
                    log.seek(0)
                    raise RuntimeError(f'{name} failed: {log.read()[-3000:]}')
            finally:
                if self.process.poll() is None:
                    self.process.send_signal(signal.SIGINT)
                    try:self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.process.kill();self.process.wait(timeout=3)
                self.process=None

    def connect(self):
        # Normal initialization only; never zero-calibrate live signals.
        self.command('pm_adcs_quick_ini',['--sample-rate',str(self.rate)],30)

    def capture(self):
        old=self.data.stat() if self.data.exists() else None
        numbers=[(int(c[4])-1)*8+int(c[-1]) for c in self.channels]
        lo=sum(1<<n for n in numbers if n<32); hi=sum(1<<(n-32) for n in numbers if n>=32)
        self.command('pm_adcs_startl',['--nsamples',str(self.count),
            '--fadc-mask-0-31',hex(lo),'--fadc-mask-32-47',hex(hi),
            '--adc24-flag',hex(1 if next(iter(self.profiles.values()))['bits']==24 else 0),
            '--temp-mask','0x0'],max(10,5*self.count/self.rate))
        now=self.data.stat()
        if old and (old.st_ino,old.st_mtime_ns,old.st_size)==(now.st_ino,now.st_mtime_ns,now.st_size):
            raise RuntimeError('ADC helper produced no fresh data; verify data path')
        values,self.last_ticks=decode_file(self.data,self.channels,self.profiles)
        if any(len(v)<self.count for v in values.values()):
            raise RuntimeError(f'ADC capture shorter than requested {self.count} samples')
        return values

    def close(self):
        if self.process is not None:
            self.process.send_signal(signal.SIGINT)
            self.process.wait(timeout=3)
