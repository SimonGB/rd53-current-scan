"""Shared live and automatic publication plots; no GUI or LaTeX dependency."""
import math
from matplotlib import rc_context
from matplotlib.lines import Line2D
from matplotlib.ticker import AutoMinorLocator

PALETTE = ['#0072B2', '#E69F00', '#009E73', '#AA4499', '#D55E00']
STYLE = {
    'text.usetex': False, 'font.family': 'serif',
    'font.serif': ['TeX Gyre Pagella', 'DejaVu Serif'],
    'mathtext.default': 'regular', 'axes.labelsize': 14,
    'axes.labelpad': 3, 'axes.linewidth': 1, 'axes.axisbelow': True,
    'axes.titlelocation': 'left', 'xtick.top': True, 'ytick.right': True,
    'xtick.direction': 'in', 'ytick.direction': 'in',
    'xtick.labelsize': 12, 'ytick.labelsize': 12,
    'xtick.major.size': 5, 'ytick.major.size': 5,
    'xtick.minor.size': 2.5, 'ytick.minor.size': 2.5,
    'grid.linestyle': '--', 'grid.alpha': .5, 'legend.fancybox': True,
    'figure.dpi': 100, 'savefig.dpi': 300,
}


def signal_kind(name):
    name = ''.join(c for c in name.lower() if c.isalnum())
    if 'vin' in name:
        return 'in'
    if 'vout' in name or 'vou' in name:
        return 'out'
    return name


def sweep_mode(rows):
    return 'voltage' if rows and rows[0].get('sweep_mode')=='voltage' else 'current'

def x_value(row):
    if row.get('sweep_mode')=='voltage':
        return float(row.get('voltage_measured_V',row.get('supply_voltage_V',float('nan'))))
    return float(row['current_measured_A'])*1000

def x_label(rows, mode_hint=None):
    mode=sweep_mode(rows) if rows else mode_hint
    return 'Measured supply voltage (V)' if mode=='voltage' else 'Measured supply current (mA)'

def voltage_drops(rows):
    """Pair unambiguous local Vin/Vout records at the SAME acquired point."""
    groups = {}
    for row in rows:
        kind = signal_kind(row['testpoint'])
        if (row['unit'] != 'V' or kind not in ('in', 'out')
                or row.get('point') is None
                or row.get('positive_endpoint') or row.get('negative_endpoint')):
            continue
        key = (row['board'], row['direction'], row['point'])
        groups.setdefault(key, {'in': [], 'out': []})[kind].append(row)
    result = []
    for pair in groups.values():
        if len(pair['in']) != 1 or len(pair['out']) != 1:
            continue  # Never choose silently between multiple Vin/Vout channels.
        vin, vout = pair['in'][0], pair['out'][0]
        if not all(math.isfinite(r[k]) for r in (vin, vout)
                   for k in ('mean', 'current_measured_A')):
            continue
        if not math.isclose(vin['current_measured_A'], vout['current_measured_A'],
                            rel_tol=1e-8, abs_tol=1e-12):
            continue
        result.append(dict(vin, mean=vin['mean'] - vout['mean'],
                           testpoint='Vin − Vout'))
    return result


def load_run(path):
    """Read a mapped run without modifying metadata or touching hardware."""
    import csv
    import json
    from pathlib import Path
    path = Path(path)
    directory = path if path.is_dir() else path.parent
    metadata_path = directory / 'metadata.json'
    metadata = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else {}
    csv_path = path if path.suffix.lower() == '.csv' else directory / 'measurements.csv'
    rows = []
    with csv_path.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        required = {'board', 'testpoint', 'unit', 'direction', 'current_measured_A', 'mean', 'point'}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError('Not a mapped measurements CSV; missing: ' + ', '.join(sorted(missing)))
        for number, row in enumerate(reader, start=2):
            row['sweep_mode']=row.get('sweep_mode') or metadata.get('settings',{}).get('sweep_mode','current')
            if row['sweep_mode'] not in ('current','voltage'):
                raise ValueError(f'Invalid sweep mode at CSV line {number}')
            if row['sweep_mode']=='voltage':
                raw=row.get('voltage_measured_V') or row.get('supply_voltage_V')
                if raw in ('',None):raise ValueError(f'Missing voltage reading at CSV line {number}')
                row['voltage_measured_V']=float(raw)
            try:
                row['current_measured_A'] = float(row['current_measured_A'])
                row['mean'] = float(row['mean'])
                row['point'] = int(row['point'])
                if row['direction'] not in ('up', 'down'):
                    raise ValueError('direction must be up or down')
            except (ValueError, TypeError) as exc:
                raise ValueError(f'Invalid measurement at CSV line {number}: {exc}') from exc
            rows.append(row)
    if not rows:
        raise ValueError('No measurement records in CSV')
    return rows, metadata.get('settings', {}), directory


def draw_sweep(figure, rows, direction, title, maximum=100, color_rows=None, configuration="", dark=False, show_difference=True, mode_hint=None):
    """One figure per direction; separate axes for different physical units."""
    figure.clear()
    boards = list(dict.fromkeys(r['board'] for r in (color_rows or rows)))
    colors = {b: PALETTE[i % len(PALETTE)] for i, b in enumerate(boards)}
    visible = [r for r in rows if r['direction'] == direction
               and math.isfinite(x_value(r))
               and math.isfinite(r['mean'])
               and 0 <= x_value(r) <= maximum]
    units = list(dict.fromkeys(r['unit'] for r in visible)) or ['V']
    drops = voltage_drops(visible) if show_difference else []
    panels = len(units) + bool(drops)
    theme = dict(STYLE)
    if dark:
        theme.update({'font.family': 'sans-serif', 'axes.facecolor': '#101f2e',
                      'axes.edgecolor': '#91a6bb', 'axes.labelcolor': '#c6d8e9',
                      'text.color': '#e2f0ff', 'xtick.color': '#c6d8e9',
                      'ytick.color': '#c6d8e9', 'grid.color': '#91a6bb',
                      'grid.alpha': .2, 'legend.facecolor': '#172b3d',
                      'legend.edgecolor': '#627b91'})
        live_palette = ['#56B4E9', '#F0E442', '#4DDBAA', '#CC99DD', '#FF9866']
        colors = {b: live_palette[i % len(live_palette)] for i, b in enumerate(boards)}
    figure.set_facecolor('#101f2e' if dark else 'white')
    with rc_context(theme):
        for index, unit in enumerate(units):
            ax = figure.add_subplot(panels, 1, index + 1)
            handles = {}
            targets = list(dict.fromkeys((r['board'], r['testpoint'])
                                        for r in visible if r['unit'] == unit))
            for board, tp in targets:
                records = [r for r in visible if
                           (r['board'], r['testpoint'], r['unit']) == (board, tp, unit)]
                records.sort(key=lambda r: r.get('point', 0))
                kind = signal_kind(tp)
                marker = 'o' if kind == 'in' else 's' if kind == 'out' else '^'
                line, = ax.plot([x_value(r) for r in records],
                                [r['mean'] for r in records], color=next((r.get('color') for r in records if r.get('color')),None) or colors[board],
                                linestyle=':' if kind == 'in' else '-', marker=marker,
                                markersize=3.5, markerfacecolor='none',
                                markeredgewidth=.9, linewidth=1.5,
                                label=f'{board}: {tp}')
                handles[(board, tp)] = line
            # Column-major legend order gives one board per row for Vin/Vout.
            ordered = []
            for board in boards:
                pairs = [(tp, h) for (b, tp), h in handles.items() if b == board]
                if not pairs:
                    continue
                pairs.sort(key=lambda pair: (0 if signal_kind(pair[0]) == 'in'
                                             else 1 if signal_kind(pair[0]) == 'out' else 2,
                                             pair[0]))
                board_handles = [h for _, h in pairs]
                if len(board_handles) % 2:
                    board_handles.append(Line2D([], [], linestyle='none', label=''))
                ordered.extend(board_handles)
            if ordered:
                ordered = ordered[::2] + ordered[1::2]
                ax.legend(ordered, [h.get_label() for h in ordered], ncol=2,
                          fontsize=9, loc='best', columnspacing=1.5,
                          handlelength=2.5, framealpha=.9)
            if configuration.strip():
                ax.margins(y=.15)
                ax.text(.03, .97, configuration, transform=ax.transAxes,
                        ha='left', va='top', fontsize=9,
                        bbox=dict(boxstyle='round,pad=.4', facecolor='#172b3d' if dark else 'white',
                                  edgecolor='#627b91' if dark else '#888888', alpha=.92))
            ax.set_title(title, fontsize=14, pad=10)
            ax.set_title(f'{direction.capitalize()} sweep', loc='right', fontsize=11, pad=10)
            ax.set_xlabel(x_label(rows, mode_hint))
            ax.set_ylabel('Voltage (V)' if unit == 'V' else
                          'Current (A)' if unit == 'A' else f'Measured value ({unit})')
            ax.set_xlim(0, maximum)
            ax.xaxis.set_minor_locator(AutoMinorLocator(2))
            ax.yaxis.set_minor_locator(AutoMinorLocator(2))
            ax.tick_params(which='both', top=True, right=True)
            ax.grid(which='major')
        if drops:
            drop_ax = figure.add_subplot(panels, 1, panels, sharex=figure.axes[0])
            for board in boards:
                records = sorted((r for r in drops if r['board'] == board), key=lambda r: r['point'])
                if records:
                    drop_ax.plot([x_value(r) for r in records],
                                 [r['mean'] for r in records], color=next((r.get('color') for r in records if r.get('color')),None) or colors[board],
                                 linestyle='-', marker='D', markersize=3,
                                 markerfacecolor='none', linewidth=1.3, label=board)
            drop_ax.set_ylabel(r'$V_\mathrm{in}-V_\mathrm{out}$ (V)')
            drop_ax.set_title('Regulator voltage drop', loc='left', fontsize=11)
            drop_ax.set_xlabel(x_label(rows, mode_hint))
            drop_ax.set_xlim(0, maximum)
            drop_ax.xaxis.set_minor_locator(AutoMinorLocator(2))
            drop_ax.yaxis.set_minor_locator(AutoMinorLocator(2))
            drop_ax.tick_params(which='both', top=True, right=True)
            drop_ax.grid(which='major')
            drop_ax.legend(fontsize=8, loc='best', ncol=min(3, len(boards)))
        figure.tight_layout()


def export_sweeps(rows, directory, title='5 RD53b in 3 serial domains', maximum=100, configuration='', show_difference=True, color_rows=None):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    for direction in ('up', 'down'):
        if not any(r['direction'] == direction for r in rows):
            continue
        with rc_context(STYLE):
            units = set(r['unit'] for r in rows if r['direction'] == direction)
            has_drop = show_difference and bool(voltage_drops([r for r in rows if r['direction'] == direction and 0 <= x_value(r) <= maximum]))
            figure = Figure(figsize=(7, 4 * (max(1, len(units)) + has_drop)), facecolor='white')
            FigureCanvasAgg(figure)
            draw_sweep(figure, rows, direction, title, maximum, configuration=configuration, show_difference=show_difference, color_rows=color_rows)
            for extension in ('pdf', 'png'):
                figure.savefig(directory / f'mapped_voltages_{direction}.{extension}',
                               dpi=300, bbox_inches='tight')


def main():
    """Regenerate plots using the run's saved settings and measurement CSV."""
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('run', type=Path, help='Run folder, metadata.json, or measurements.csv')
    parser.add_argument('--title', help='Override saved plot title')
    parser.add_argument('--configuration', help='Override configuration box text')
    parser.add_argument('--no-configuration', action='store_true', help='Hide the configuration box')
    parser.add_argument('--max-ma', type=float, help='Upper plot limit in mA')
    parser.add_argument('--max-v', type=float, help='Upper plot voltage limit in V')
    parser.add_argument('--no-difference', action='store_true', help='Hide Vin − Vout subplot')
    parser.add_argument('--output', type=Path, help='Output folder; default is the run folder')
    args = parser.parse_args()
    rows, settings, directory = load_run(args.run)
    title = args.title if args.title is not None else settings.get('plot_title', 'RD53b current scan')
    maximum = (args.max_v if args.max_v is not None else float(settings.get('plot_max_v',2.0))) if sweep_mode(rows)=='voltage' else (args.max_ma if args.max_ma is not None else float(settings.get('plot_max_ma',100)))
    configuration = (args.configuration if args.configuration is not None
                     else settings.get('plot_configuration', ''))
    if args.no_configuration or (args.configuration is None and settings.get('plot_hide_configuration', False)):
        configuration = ''
    if not math.isfinite(maximum) or maximum <= 0:
        parser.error('--max-ma must be a positive finite number')
    output = args.output or directory
    output.mkdir(parents=True, exist_ok=True)
    export_sweeps(rows, output, title, maximum, configuration,
                  show_difference=not args.no_difference and not settings.get('plot_hide_difference', False))
    print(f'Saved up/down PDF and PNG plots in {output}')


if __name__ == '__main__':
    main()
