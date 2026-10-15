"""Bulk board setup and deterministic type-aware wiring proposals."""
import copy
import re


def signal_unit(point):
    return point.get('unit','A' if point['name'].lower() in ('iin','iout','iload','ishunt') else 'V')


def add_boards(profile,count,template=None,prefix='board'):
    if not 1<=count<=200:raise ValueError('Choose between 1 and 200 boards')
    result=copy.deepcopy(profile)
    template=copy.deepcopy(template or result['boards'][0])
    used={b['id'] for b in result['boards']};index=1;added=[]
    for _ in range(count):
        while f'{prefix}{index}' in used:index+=1
        b=copy.deepcopy(template);b['id']=f'{prefix}{index}';used.add(b['id']);index+=1
        result['boards'].append(b);added.append(b['id'])
    return result,added


def delete_testpoint(profile,board_id,name):
    result=copy.deepcopy(profile)
    board=next(b for b in result['boards'] if b['id']==board_id)
    board['testpoints']=[p for p in board['testpoints'] if p['name']!=name]
    result['mappings']=[m for m in result['mappings'] if (m['board'],m['testpoint'])!=(board_id,name)]
    return result


def auto_assign(profile,profiles,modules,replace=False):
    """Propose unused fitted inputs only; never claim to switch physical wiring."""
    result=copy.deepcopy(profile)
    if result['backend']!='venelin':raise ValueError('Bulk auto-assignment uses the Venelin ADC backend')
    if not modules or len(set(modules))!=len(modules) or any(m not in range(1,7) for m in modules):
        raise ValueError('List installed PMOD module numbers 1..6, for example 1,2')
    if replace:result['mappings']=[]
    used={m['channel'] for m in result['mappings']}
    mapped={(m['board'],m['testpoint']) for m in result['mappings']}
    candidates=sorted((c for c in profiles if int(c[4]) in modules),key=lambda c:(int(c[4]),int(c[-1])))
    pending=[]
    for b in result['boards']:
        if not b.get('enabled',True):continue
        for p in b['testpoints']:
            if (b['id'],p['name']) in mapped:continue
            unit=signal_unit(p)
            # Prefer low-range differential inputs for local regulator voltages.
            fits=[c for c in candidates if c not in used and profiles[c]['kind']==('I' if unit=='A' else 'dV')]
            if not fits:raise ValueError(f'No unused fitted {unit} input for {b["id"]} / {p["name"]}. Select more installed PMODs or fewer testpoints.')
            channel=fits[0];used.add(channel)
            pending.append(dict(board=b['id'],testpoint=p['name'],channel=channel,probe=1,range=2))
    result['mappings'].extend(pending)
    return result


def active_profile(profile):
    result=copy.deepcopy(profile)
    result['boards']=[b for b in result['boards'] if b.get('enabled',True)]
    ids={b['id'] for b in result['boards']}
    result['mappings']=[m for m in result['mappings'] if m['board'] in ids]
    return result
