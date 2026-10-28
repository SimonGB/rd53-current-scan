"""Signal-owned colors and channel-owned Pico settings."""
import copy

DEFAULT_COLORS={'vin':'#e53935','vout':'#43a047','vref':'#1e88e5','vrefpre':'#ec407a','vprereg':'#ab47bc','iin':'#fb8c00'}
PALETTE=['#26a69a','#ffa726','#7e57c2','#29b6f6','#d4e157','#ef5350']

def initialize_colors(profile):
    for board in profile['boards']:
        for i,p in enumerate(board['testpoints']):
            if not p.get('color'):
                old=next((m.get('color') for m in profile.get('mappings',[]) if m['board']==board['id'] and m['testpoint']==p['name'] and m.get('color')),None)
                p['color']=old or DEFAULT_COLORS.get(p['name'].lower(),PALETTE[i%len(PALETTE)])
    return profile

def point_color(profile,board,name):
    return next((p.get('color','') for b in profile['boards'] if b['id']==board for p in b['testpoints'] if p['name']==name),'')

def assign_probe(profile,channel,target):
    """Replace a cable assignment, preserving colors on every physical marker."""
    p=initialize_colors(copy.deepcopy(profile));mappings=p['mappings']
    old=next((m for m in mappings if m['channel']==channel),{})
    settings=p.setdefault('pico_channels',{})
    for m in mappings:
        if m['channel'] in 'ABCD':settings[m['channel']]={k:m.get(k,default) for k,default in [('probe',1),('range',2)]}
    settings[channel]={k:old.get(k,settings.get(channel,{}).get(k,default)) for k,default in [('probe',1),('range',2)]}
    if target is not None and not any(b['id']==target[0] and any(t['name']==target[1] for t in b['testpoints']) for b in p['boards']):raise ValueError('Unknown testpoint')
    # Selecting an already wired marker moves that assignment to this channel.
    p['mappings']=[m for m in mappings if m['channel']!=channel and (target is None or (m['board'],m['testpoint'])!=tuple(target))]
    if target:
        p['mappings'].append(dict(board=target[0],testpoint=target[1],channel=channel,color=point_color(p,*target),**settings[channel]))
    return p
