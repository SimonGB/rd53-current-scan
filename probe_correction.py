"""Reversible post-acquisition probe scaling; stored samples stay unchanged."""
import json
import math
from pathlib import Path

STATS=('mean','capture_std','sample_std','within_capture_rms','peak_to_peak')

def factor(value):
    value=float(value)
    if not math.isfinite(value) or value<=0:raise ValueError('Probe factor must be positive and finite')
    return value


def read_corrections(directory):
    path=Path(directory)/'probe_corrections.json'
    result=json.loads(path.read_text()) if path.exists() else {}
    return {channel:factor(value) for channel,value in result.items()}


def scale_rows(rows,profile,corrections):
    if profile.get('backend')!='pico':return rows
    mapping={(m['board'],m['testpoint']):m for m in profile.get('mappings',[])}
    for row in rows:
        m=mapping.get((row['board'],row['testpoint']))
        if not m:continue
        original=factor(m.get('probe',1));target=factor(corrections.get(m['channel'],original))
        previous=factor(row.get('_display_probe_factor',original));ratio=target/previous
        for key in STATS:
            if row.get(key) not in ('',None):row[key]=float(row[key])*ratio
        row['_display_probe_factor']=target
    return rows


def waveform_view(records,profile,corrections):
    """Make display copies from original arrays, never rescale stored records."""
    mapping={m['channel']:m for m in profile['mappings']}
    return [dict(r,values={ch:values*(factor(corrections.get(ch,mapping[ch].get('probe',1)))/factor(mapping[ch].get('probe',1))) for ch,values in r['values'].items()}) for r in records]


def edit_corrections(parent,profile,current):
    from PyQt6.QtWidgets import QDialog,QVBoxLayout,QLabel,QFormLayout,QDoubleSpinBox,QDialogButtonBox
    dialog=QDialog(parent);dialog.setWindowTitle('Correct saved probe factors')
    layout=QVBoxLayout(dialog);note=QLabel('Enter the actual probe factor. A change from ×1 to ×10 multiplies voltage by 10. Original data remain unchanged. This cannot recover clipped signals or bandwidth lost during acquisition.');note.setWordWrap(True);layout.addWidget(note)
    form=QFormLayout();layout.addLayout(form);boxes={}
    for m in profile['mappings']:
        original=factor(m.get('probe',1));box=QDoubleSpinBox();box.setDecimals(4);box.setRange(.0001,100000);box.setValue(current.get(m['channel'],original));boxes[m['channel']]=box
        form.addRow(f"{m['board']} / {m['testpoint']} ({m['channel']}, recorded ×{original:g})",box)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
    if dialog.exec()!=QDialog.DialogCode.Accepted:return None
    return {ch:box.value() for ch,box in boxes.items()}
