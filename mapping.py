"""Serializable wiring assignments. Physical wiring is never switched by software."""
import json
import math
import re
from pathlib import Path


def validate_profile(profile):
    from connections import endpoints, connection_points
    if profile.get('backend') not in ('pico', 'venelin'):
        raise ValueError('Backend must be pico or venelin')
    boards = profile.get('boards', [])
    ids = [b['id'] for b in boards]
    if not ids or len(ids) != len(set(ids)) or any(not x for x in ids):
        raise ValueError('Board IDs must be present and unique')
    for b in boards:
        points = b.get('testpoints', [])
        names = [p['name'] for p in points]
        if len(names) != len(set(names)):
            raise ValueError('Testpoint names must be unique per board')
        for p in points:
            if not p['name'] or any(not math.isfinite(float(p[n])) or not 0<=float(p[n])<=1 for n in ('x','y')):
                raise ValueError('Testpoints need names and normalized coordinates')
    mappings = profile.get('mappings', [])
    if not mappings:
        raise ValueError('Assign at least one measurement input')
    used, targets = set(), set()
    valid_endpoints=endpoints(profile)
    for point in connection_points(profile).values():
        if point.get('positive') not in valid_endpoints or point.get('negative') not in valid_endpoints:
            raise ValueError('Connection references an unknown endpoint')
        if point['positive']==point['negative']:
            raise ValueError('Connection endpoints must differ')
    for m in mappings:
        if m['board'] not in ids:
            raise ValueError('Unknown board ID')
        b = boards[ids.index(m['board'])]
        if m['testpoint'] not in [p['name'] for p in b['testpoints']]:
            raise ValueError('Unknown testpoint')
        channel = m['channel']
        if profile['backend']=='pico':
            if channel not in 'ABCD' or len(channel)!=1:
                raise ValueError('6426E channel must be A..D')
        elif not re.fullmatch(r'PMOD[1-6]_Ch[0-7]', channel):
            raise ValueError('ADC channel must be PMOD1..6_Ch0..7')
        if channel in used or (m['board'],m['testpoint']) in targets:
            raise ValueError('Each input and target may be assigned only once')
        used.add(channel); targets.add((m['board'],m['testpoint']))
        for n in ('probe','range'):
            if not math.isfinite(float(m.get(n,1))) or float(m.get(n,1))<=0:
                raise ValueError('Probe factor and range must be positive')
    if profile['backend']=='pico' and not 1 <= len(mappings) <= 4:
        raise ValueError('PicoScope supports 1 to 4 mapped inputs (A–D)')
    return profile


def default_profile():
    return {'backend':'pico','boards':[{'id':'board1','image':'','testpoints':[
        {'name':'Vref','x':.3,'y':.65},{'name':'Vout','x':.7,'y':.65}]}],
        'mappings':[{'board':'board1','testpoint':'Vref','channel':'A','probe':1,'range':2},
                    {'board':'board1','testpoint':'Vout','channel':'B','probe':1,'range':2}]}


def load_profile(path):
    return validate_profile(json.loads(Path(path).read_text()))

