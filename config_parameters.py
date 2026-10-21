"""Discover numeric configuration assignments for sweep comparisons.

Only named assignments (e.g. Rext=750 Ohm or Vofshalf=0.4 V) are
considered. Do not accidentally treat fitted Vofs as configured Vofshalf.
"""
import json
from pathlib import Path
import re
from collections import Counter

# Match a named value at the beginning of a configuration or following a
# comma/semicolon or the ' - ' separator used in the original RD53 run notes.
ASSIGNMENT = re.compile(
    r"(?P<separator>^|[,;]|\s+-\s+)\s*"
    r"(?P<name>[A-Za-z][A-Za-z0-9_ ]{0,35}?)\s*[:=]\s*"
    r"(?P<value>[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+))\s*"
    r"(?P<unit>k\s*(?:ohms?|Ω|Ω)|(?:ohms?|Ω|Ω)|m\s*V|V)?"
    r"(?=\s*(?:[,;]|$))",
    re.IGNORECASE,
)


def normalized_name(name):
    normalized = re.sub(r'[_\s]+', '', name).casefold()
    if normalized in ('voffset', 'voff', 'offsetvoltage'):
        return 'offset'
    return normalized


def extract_assignments(configuration):
    """Yield parameter dictionaries including full source spans for grouping."""
    if not isinstance(configuration, str):
        return []
    assignments = []
    for match in ASSIGNMENT.finditer(configuration):
        label = match.group('name').strip()
        name = normalized_name(label)
        raw_unit = (match.group('unit') or '').replace(' ', '').casefold()
        value = float(match.group('value').replace(',', '.'))
        if raw_unit in ('kohm', 'kohms', 'kω', 'kΩ'):
            value *= 1000
            unit = 'Ω'
        elif raw_unit in ('ohm', 'ohms', 'ω', 'Ω'):
            unit = 'Ω'
        elif raw_unit == 'mv':
            value /= 1000
            unit = 'V'
        elif raw_unit == 'v':
            unit = 'V'
        else:
            unit = ''
        assignments.append(dict(name=name,label=label,value=value,unit=unit,
                                start=match.start('value'),end=match.end('unit') if match.group('unit') else match.end('value')))
    return assignments


def variable_family(configuration, parameter):
    """Return value, normalized unit and remaining configuration for exactly one key."""
    entries = [x for x in extract_assignments(configuration) if x['name'] == normalized_name(parameter)]
    if len(entries) != 1:
        return None
    entry = entries[0]
    family = (configuration[:entry['start']] + '<varying>' + configuration[entry['end']:])
    family = re.sub(r'\s+', ' ', family).strip().casefold()
    return entry['value'], family, entry['unit']


def discovered_parameters(root):
    """Rank names by appearance in saved acquisition configurations, no hardware."""
    counts, examples = Counter(), {}
    root = Path(root).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    for path in root.rglob('metadata.json'):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            config = data.get('settings', {}).get('plot_configuration', '')
            observed = set()
            for entry in extract_assignments(config):
                key = entry['name']
                if key in observed:
                    continue
                counts[key] += 1
                observed.add(key)
                examples.setdefault(key,(entry['label'], entry['unit']))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return [(key, examples[key][0], examples[key][1], counts[key])
            for key in sorted(counts, key=lambda k: (-counts[k], k))]
