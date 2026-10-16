"""Named physical differential measurements; never subtract unrelated captures."""
import copy

GROUP = 'Connections'


def endpoints(profile):
    result = [{'board': None, 'point': 'GND'}]
    for board in profile['boards']:
        if board.get('kind') == 'connections':
            continue
        for name in dict.fromkeys(['GND'] + [p['name'] for p in board['testpoints']]):
            result.append({'board': board['id'], 'point': name})
    return result


def endpoint_label(endpoint):
    return f"{endpoint['board'] or 'Global'} / {endpoint['point']}"


def connection_label(positive, negative):
    return endpoint_label(positive) + ' − ' + endpoint_label(negative)


def add_connection(profile, positive, negative, channel):
    result = copy.deepcopy(profile)
    if positive not in endpoints(result) or negative not in endpoints(result):
        raise ValueError('Choose existing endpoints')
    if positive == negative:
        raise ValueError('Choose two different endpoints')
    if any(m['channel'] == channel for m in result['mappings']):
        raise ValueError('Choose an unused measurement input')
    group = next((b for b in result['boards'] if b.get('kind') == 'connections'), None)
    if group is None:
        if any(b['id'] == GROUP for b in result['boards']):
            raise ValueError('Rename the board named Connections first')
        group = dict(id=GROUP, kind='connections', image='', testpoints=[])
        result['boards'].append(group)
    label = connection_label(positive, negative)
    if any(p['name'] == label for p in group['testpoints']):
        raise ValueError('This connection already exists')
    group['testpoints'].append(dict(name=label, x=.5, y=.5, unit='V',
                                   positive=positive, negative=negative))
    result['mappings'].append(dict(board=group['id'], testpoint=label, channel=channel, probe=1, range=2))
    return result


def connection_points(profile):
    return {(b['id'], p['name']): p for b in profile['boards']
            if b.get('kind') == 'connections' for p in b['testpoints']}


def prune_connections(profile):
    """Remove links whose physical endpoint was deleted."""
    valid = endpoints(profile)
    for b in profile['boards']:
        if b.get('kind') == 'connections':
            b['testpoints'] = [p for p in b['testpoints'] if
                               p.get('positive') in valid and p.get('negative') in valid]
    targets = {(b['id'], p['name']) for b in profile['boards'] for p in b['testpoints']}
    profile['mappings'] = [m for m in profile['mappings'] if (m['board'], m['testpoint']) in targets]
    return profile
