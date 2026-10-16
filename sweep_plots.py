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


def draw_sweep(figure, rows, direction, title, maximum=100, color_rows=None):
    """One figure per direction; separate axes for different physical units."""
    figure.clear()
    boards = list(dict.fromkeys(r['board'] for r in (color_rows or rows)))
    colors = {b: PALETTE[i % len(PALETTE)] for i, b in enumerate(boards)}
    visible = [r for r in rows if r['direction'] == direction
               and math.isfinite(r['current_measured_A'])
               and math.isfinite(r['mean'])
               and 0 <= r['current_measured_A'] * 1000 <= maximum]
    units = list(dict.fromkeys(r['unit'] for r in visible)) or ['V']
    with rc_context(STYLE):
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


def export_sweeps(rows, directory, title='5 RD53b in parallel', maximum=100):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    for direction in ('up', 'down'):
        if not any(r['direction'] == direction for r in rows):
            continue
        with rc_context(STYLE):
            units = set(r['unit'] for r in rows if r['direction'] == direction)
            figure = Figure(figsize=(7, 5 * max(1, len(units))), facecolor='white')
            FigureCanvasAgg(figure)
            draw_sweep(figure, rows, direction, title, maximum)
            for extension in ('pdf', 'png'):
                figure.savefig(directory / f'mapped_voltages_{direction}.{extension}',
                               dpi=300, bbox_inches='tight')
