"""Overlay saved measurements without editing acquisition files."""
import json
import math
from pathlib import Path
import numpy as np
from sweep_plots import load_run,x_value,sweep_mode
from probe_correction import read_corrections,waveform_view


def load_measurement(path):
    path=Path(path);folder=path if path.is_dir() else path.parent
    meta=json.loads((folder/'metadata.json').read_text()) if (folder/'metadata.json').exists() else {}
    label=folder.parent.name+' / '+folder.name
    if 'captures' in meta and 'profile' in meta:
        profile=meta['profile'];records=[]
        for entry in meta['captures']:
            with np.load(folder/entry['file'],allow_pickle=False) as f:
                records.append(dict(current_ma=entry['current_ma'],voltage_limit_v=entry.get('voltage_limit_v',meta['settings'].get('voltage_limit')),repeat=entry['repeat'],time_s=f['time_s'].copy(),values={m['channel']:f[m['channel']].copy() for m in profile['mappings']}))
        if not records:raise ValueError('No completed waveforms in this run')
        records=waveform_view(records,profile,read_corrections(folder))
        series=[]
        from testpoint_pinning import point_color
        for r in records:
            for m in profile['mappings']:
                series.append(dict(signal=m['testpoint'],board=m['board'],unit='V',x=r['time_s']*1000,y=r['values'][m['channel']],color=point_color(profile,m['board'],m['testpoint']) or m.get('color',''),direction='',detail=f"{r['current_ma']:g} mA / {r['voltage_limit_v']} V / #{r['repeat']}"))
        return dict(kind='startup',xlabel='Time from trigger [ms]',series=series,path=str(folder.resolve()),label=label)
    rows,_,folder=load_run(path);mode=sweep_mode(rows)
    series=[]
    for board,signal,unit,direction in dict.fromkeys((r['board'],r['testpoint'],r['unit'],r['direction']) for r in rows):
        rr=[r for r in rows if (r['board'],r['testpoint'],r['unit'],r['direction'])==(board,signal,unit,direction)]
        series.append(dict(signal=signal,board=board,unit=unit,x=np.array([x_value(r) for r in rr]),y=np.array([r['mean'] for r in rr]),color=next((r.get('color') for r in rr if r.get('color')),''),direction=direction,detail=direction))
    return dict(kind='dc_'+mode,xlabel='Measured supply voltage [V]' if mode=='voltage' else 'Measured supply current [mA]',series=series,path=str(folder.resolve()),label=label)


def compatible(runs,new):
    if runs and runs[0]['kind']!=new['kind']:raise ValueError('Compare startup waveforms, DC current sweeps or DC voltage sweeps separately; their x axes differ.')


def draw_comparison(figure,runs,signal=None,direction='both'):
    figure.clear()
    selected=[(i,r,s) for i,r in enumerate(runs) for s in r['series'] if (signal is None or s['signal']==signal) and (direction=='both' or not s['direction'] or s['direction']==direction)]
    units=list(dict.fromkeys(s['unit'] for _,_,s in selected)) or ['V']
    axes=figure.subplots(len(units),1,sharex=True,squeeze=False)[:,0]
    from matplotlib import colormaps
    signals=list(dict.fromkeys(s['signal'] for _,_,s in selected))
    colors={name:next((s['color'] for _,_,s in selected if s['signal']==name and s['color']),colormaps['tab10'](j%10)) for j,name in enumerate(signals)}
    lines=['-','--','-.',':'];markers=['','o','s','^','D','v','x','+']
    for ax,unit in zip(axes,units):
        for i,r,s in selected:
            if s['unit']!=unit:continue
            label=f"{r['label']} · {s['board']} / {s['signal']} · {s['detail']}"
            ax.plot(s['x'],s['y'],color=colors[s['signal']],linestyle=lines[i%4],marker=markers[(i//4)%len(markers)],markevery=max(1,len(s['x'])//12),linewidth=1,alpha=.8,label=label)
        ax.set_ylabel(unit);ax.grid(alpha=.25)
        if ax.lines:ax.legend(fontsize=7)
    axes[-1].set_xlabel(runs[0]['xlabel'] if runs else 'Load measurements to compare')
    figure.tight_layout()
