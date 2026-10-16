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


def draw_sweep(figure, rows, direction, title, maximum=100, color_rows=None, configuration="", dark=False):
    """One figure per direction; separate axes for different physical units."""
    figure.clear()
    boards = list(dict.fromkeys(r['board'] for r in (color_rows or rows)))
    colors = {b: PALETTE[i % len(PALETTE)] for i, b in enumerate(boards)}
    visible = [r for r in rows if r['direction'] == direction
               and math.isfinite(r['current_measured_A'])
               and math.isfinite(r['mean'])
               and 0 <= r['current_measured_A'] * 1000 <= maximum]
    units = list(dict.fromkeys(r['unit'] for r in visible)) or ['V']
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
            ax = figure.add_subplot(len(units), 1, index + 1)
            handles = {}
            targets = list(dict.fromkeys((r['board'], r['testpoint'])
                                        for r in visible if r['unit'] == unit))
            for board, tp in targets:
                records = [r for r in visible if
                           (r['board'], r['testpoint'], r['unit']) == (board, tp, unit)]
                records.sort(key=lambda r: r.get('point', 0))
                kind = signal_kind(tp)
                marker = 'o' if kind == 'in' else 's' if kind == 'out' else '^'
                line, = ax.plot([r['current_measured_A'] * 1000 for r in records],
                                [r['mean'] for r in records], color=colors[board],
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
            ax.set_xlabel('Measured supply current (mA)')
            ax.set_ylabel('Voltage (V)' if unit == 'V' else
                          'Current (A)' if unit == 'A' else f'Measured value ({unit})')
            ax.set_xlim(0, maximum)
            ax.xaxis.set_minor_locator(AutoMinorLocator(2))
            ax.yaxis.set_minor_locator(AutoMinorLocator(2))
            ax.tick_params(which='both', top=True, right=True)
            ax.grid(which='major')
        figure.tight_layout()


def export_sweeps(rows, directory, title='5 RD53b in 3 serial domains', maximum=100, configuration=''):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    for direction in ('up', 'down'):
        if not any(r['direction'] == direction for r in rows):
            continue
        with rc_context(STYLE):
            units = set(r['unit'] for r in rows if r['direction'] == direction)
            figure = Figure(figsize=(7, 5 * max(1, len(units))), facecolor='white')
            FigureCanvasAgg(figure)
            draw_sweep(figure, rows, direction, title, maximum, configuration=configuration)
            for extension in ('pdf', 'png'):
                figure.savefig(directory / f'mapped_voltages_{direction}.{extension}',
                               dpi=300, bbox_inches='tight')


def main():
    """Regenerate plots using the run's saved settings and measurement CSV."""
    import argparse
    import csv
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('run', type=Path, help='Run folder, metadata.json, or measurements.csv')
    parser.add_argument('--title', help='Override saved plot title')
    parser.add_argument('--configuration', help='Override configuration box text')
    parser.add_argument('--no-configuration', action='store_true', help='Hide the configuration box')
    parser.add_argument('--max-ma', type=float, help='Upper plot limit in mA')
    parser.add_argument('--output', type=Path, help='Output folder; default is the run folder')
    args = parser.parse_args()
    directory = args.run if args.run.is_dir() else args.run.parent
    metadata_path = directory / 'metadata.json'
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    settings = metadata.get('settings', {})
    title = args.title if args.title is not None else settings.get('plot_title', 'RD53b current scan')
    maximum = args.max_ma if args.max_ma is not None else float(settings.get('plot_max_ma', 100))
    configuration = (args.configuration if args.configuration is not None
                     else settings.get('plot_configuration', ''))
    if args.no_configuration or (args.configuration is None and settings.get('plot_hide_configuration', False)):
        configuration = ''
    if not math.isfinite(maximum) or maximum <= 0:
        parser.error('--max-ma must be a positive finite number')
    csv_path = args.run if args.run.suffix.lower() == '.csv' else directory / 'measurements.csv'
    rows = []
    with csv_path.open(newline='') as handle:
        for row in csv.DictReader(handle):
            row['current_measured_A'] = float(row['current_measured_A'])
            row['mean'] = float(row['mean'])
            row['point'] = int(row['point'])
            rows.append(row)
    if not rows:
        parser.error('No measurement records in CSV')
    output = args.output or directory
    output.mkdir(parents=True, exist_ok=True)
    export_sweeps(rows, output, title, maximum, configuration)
    print(f'Saved up/down PDF and PNG plots in {output}')


if __name__ == '__main__':
    main()
