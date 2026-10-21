"""Compare RD53 input-current sweeps while varying external resistor Rext.

Runs are discovered recursively from results/<run-name>/<timestamp>/measurements.csv.
The acquisition's original plot_configuration (metadata.json) is used to find
Rext; saved presentation changes do not silently reclassify a measurement.
No hardware is accessed. The original run data is never modified.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from collections import defaultdict

import numpy as np
from sweep_plots import load_run

# Examples: Rext = 750 Ohm, R_ext: 0.75 kOhm, Rext=750Ω
REXT_PATTERN = re.compile(
    r"(?<!\w)R[_\s]*ext\s*[:=]\s*"
    r"(?P<value>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>k\s*(?:ohms?|Ω|Ω)|(?:ohms?|Ω|Ω))?(?!\w)",
    flags=re.IGNORECASE,
)


# Offset-voltage setpoints are experimental configuration values, not the
# intercept Vofs extracted from fitting the VIN-versus-current relationship.
OFFSET_PATTERN = re.compile(
    r"(?<!\\w)(?:V[_\\s]*(?:offset|off|ofs)|offset[_\\s]*voltage)\\s*[:=]\\s*"
    r"(?P<value>[+-]?(?:\\d+(?:[.,]\\d*)?|[.,]\\d+))\\s*"
    r"(?P<unit>m\\s*V|V)?(?!\\w)",
    flags=re.IGNORECASE,
)


def parse_variable(configuration, parameter='rext'):
    """Parse one changed parameter and normalize all other configuration text.

    Parameter='offset' keeps the actual Rext assignment in the family key,
    so configurations with different external resistors cannot be mixed.
    """
    if parameter == 'rext':
        return parse_configuration(configuration)
    if parameter != 'offset':
        raise ValueError(f'Unknown comparison parameter: {parameter}')
    matches = list(OFFSET_PATTERN.finditer(configuration or ''))
    if len(matches) != 1:
        return None
    match = matches[0]
    value = float(match.group('value').replace(',', '.'))
    unit = (match.group('unit') or 'V').replace(' ', '').lower()
    volts = value / 1000 if unit == 'mv' else value
    if not math.isfinite(volts):
        return None
    family = OFFSET_PATTERN.sub('Voffset=<varying>', configuration)
    family = re.sub(r'\\s+', ' ', family).strip().casefold()
    return volts, family


def parse_configuration(configuration: str):
    """Return (Rext in ohms, normalized configuration family), or None."""
    if not configuration:
        return None
    matches = list(REXT_PATTERN.finditer(configuration))
    if len(matches) != 1:
        return None
    match = matches[0]
    value = float(match.group('value').replace(',', '.'))
    unit = (match.group('unit') or 'ohm').lower().replace(' ', '')
    if not math.isfinite(value) or value <= 0:
        return None
    ohms = value * (1000 if unit.startswith('k') else 1)
    family = REXT_PATTERN.sub('Rext=<varying>', configuration)
    family = re.sub(r'\s+', ' ', family).strip().casefold()
    return ohms, family


@dataclass
class Run:
    directory: Path
    board: str
    rext_ohms: float
    family: str
    configuration: str
    rows: list
    mtime: float
    parameter: str = 'rext'

    @property
    def variable_value(self):
        return self.rext_ohms

    @property
    def signals(self):
        return tuple(dict.fromkeys(
            r['testpoint'] for r in self.rows if r.get('unit') == 'V'))

    @property
    def run_label(self):
        return self.directory.name


def discover(root, return_errors=False, parameter='rext'):
    """Discover compatible current scans, deduplicating by CSV path."""
    root = Path(root).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(f'Not a results directory: {root}')
    runs, errors = [], []
    for csv_path in sorted(root.rglob('measurements.csv')):
        directory = csv_path.parent
        metadata_path = directory / 'metadata.json'
        if not metadata_path.is_file():
            errors.append(f'{directory}: missing metadata.json')
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
            acquired = metadata.get('settings', {})
            # Use original acquisition metadata, NOT a replotted sidecar.
            configuration = str(acquired.get('plot_configuration') or '').strip()
            parsed = parse_variable(configuration, parameter)
            if parsed is None:
                errors.append(f'{directory}: no unique '+('Rext' if parameter=='rext' else 'offset voltage')+' in acquisition configuration')
                continue
            rext, family = parsed
            rows, _, _ = load_run(directory)
            if any(r.get('sweep_mode') != 'current' for r in rows):
                errors.append(f'{directory}: not a current sweep')
                continue
            for board in dict.fromkeys(r['board'] for r in rows):
                filtered = [
                    r for r in rows
                    if r['board'] == board and r.get('unit') == 'V'
                    and r.get('direction') in ('up', 'down')
                ]
                if not filtered:
                    continue
                runs.append(Run(directory, board, rext, family,
                                configuration, filtered, csv_path.stat().st_mtime, parameter))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            errors.append(f'{directory}: {exc}')
    return (runs, errors) if return_errors else runs


def families(runs):
    """One family per board + identical configuration except Rext."""
    grouped = defaultdict(list)
    for run in runs:
        grouped[(run.board, run.family, run.parameter)].append(run)
    return {key: sorted(group, key=lambda r: (r.variable_value, r.mtime))
            for key, group in grouped.items()}


def signal_choices(runs):
    """Voltages common to every run; avoid silently omitting measurements."""
    if not runs:
        return []
    common = set(runs[0].signals)
    for run in runs[1:]:
        common &= set(run.signals)
    return sorted(common, key=lambda n: (n.casefold() not in ('vin', 'v_in'), n.casefold()))


def trace(run, testpoint, direction='up'):
    """Return measured input-current (A) and chosen voltage (V), sorted by I."""
    rows = [r for r in run.rows if r['testpoint'] == testpoint and
            r['unit'] == 'V' and r['direction'] == direction]
    points = [(float(r['current_measured_A']), float(r['mean']))
              for r in rows]
    points = [(x, y) for x, y in points if math.isfinite(x) and math.isfinite(y)]
    points.sort(key=lambda p: p[0])
    if not points:
        raise ValueError(f'{run.directory}: no {direction} values for {testpoint}')
    return np.array(points, dtype=float).T


def fit_trace(x, y, min_ma=None, max_ma=None, max_voltage=2.0):
    """Fit V = R_slope * I + Vofs within an explicit x/y selection.

    x is measured amperes; fitted slope therefore has units V/A = ohms.
    No automatic inference that every point below 2 V belongs to a
    linear operating region is made. The returned mask marks used samples.
    """
    mask = np.isfinite(x) & np.isfinite(y)
    if min_ma is not None:
        mask &= x * 1000 >= min_ma
    if max_ma is not None:
        mask &= x * 1000 <= max_ma
    if max_voltage is not None:
        mask &= y <= max_voltage
    xf, yf = x[mask], y[mask]
    if len(np.unique(xf)) < 3:
        raise ValueError('Fit needs at least 3 distinct currents in the selected interval')
    (slope, offset), covariance = np.polyfit(xf, yf, 1, cov=True)
    slope_error, offset_error = np.sqrt(np.diag(covariance))
    prediction = slope * xf + offset
    rss = float(np.sum((yf - prediction)**2))
    tss = float(np.sum((yf - yf.mean())**2))
    r2 = 1 - rss/tss if tss > 0 else float('nan')
    return dict(slope_ohm=float(slope), slope_error_ohm=float(slope_error),
                offset_v=float(offset), offset_error_v=float(offset_error),
                r2=r2, rmse_v=float(np.sqrt(rss/len(xf))),
                points=len(xf), mask=mask)


def compare(runs, signal='Vin', direction='up', fit=False,
            fit_min_ma=None, fit_max_ma=None, fit_max_voltage=2.0,
            max_plot_ma=None, title=None, figure=None, draw_fit_lines=True):
    """Return a Matplotlib Figure and fit result table."""
    from matplotlib.figure import Figure
    from matplotlib import colormaps
    if not runs:
        raise ValueError('Select at least one run')
    parameter = runs[0].parameter
    if any(run.parameter != parameter for run in runs):
        raise ValueError('Do not mix Rext and offset-voltage comparisons')
    if signal not in signal_choices(runs):
        raise ValueError(f'{signal} is not present in every selected run')
    if direction not in ('up', 'down'):
        raise ValueError('Direction must be up or down')

    if figure is None:
        figure = Figure(figsize=(9.0, 5.8), layout='constrained')
    else:
        figure.clear()
    ax = figure.add_subplot(111)
    cmap = colormaps['tab20']
    colors = [cmap(i / max(1, len(runs)-1)) for i in range(len(runs))]
    duplicate_count = defaultdict(int)
    for run in runs:
        duplicate_count[run.variable_value] += 1

    fits = []
    for index, run in enumerate(sorted(runs, key=lambda r:(r.variable_value,r.mtime))):
        x_full, y_full = trace(run, signal, direction)
        # Axis visibility is independent of the fit interval. In particular,
        # changing the graph limit must not alter Figure 4.16 fit parameters.
        plot_mask = (x_full * 1000 <= max_plot_ma) if max_plot_ma is not None else np.ones(len(x_full),dtype=bool)
        x, y = x_full[plot_mask], y_full[plot_mask]
        color = colors[index]
        label = (f'Rext = {run.variable_value:g} Ω' if parameter=='rext'
                 else f'Voffset = {run.variable_value:g} V')
        if duplicate_count[run.variable_value] > 1:
            label += f' ({run.run_label})'
        if len(x):
            ax.plot(x * 1000, y, color=color, marker='o', markersize=3.0,
                markerfacecolor='none', linewidth=1.4, label=label)
        if fit:
            try:
                result = fit_trace(x_full, y_full, fit_min_ma, fit_max_ma, fit_max_voltage)
            except ValueError as exc:
                fits.append(dict(rext_ohm=run.variable_value if parameter=='rext' else '',
                                 offset_setting_v=run.variable_value if parameter=='offset' else '',
                                 parameter=parameter, run=str(run.directory),
                                 status=str(exc)))
                continue
            region = result.pop('mask')
            x_fit = x_full[region]
            domain = np.array([x_fit.min(), x_fit.max()])
            if draw_fit_lines:
                ax.plot(domain*1000,
                        result['slope_ohm']*domain + result['offset_v'],
                        color=color, linestyle='--', linewidth=1.9)
            fits.append(dict(rext_ohm=run.rext_ohms, run=str(run.directory),
                             status='ok', **result))
    ax.set_xlabel('Measured input current (mA)')
    ax.set_ylabel(f'{signal} (V)')
    name = 'Rext' if parameter=='rext' else 'offset voltage'
    ax.set_title(title or f'{runs[0].board}: {signal} versus input current — varying {name}')
    ax.grid(alpha=.3)
    if max_plot_ma is not None:
        ax.set_xlim(0, max_plot_ma)
    ax.legend(loc='best', fontsize=8, ncol=2)
    return figure, fits


def plot_fit_parameters(fits, title='Rext dependence of fitted parameters', figure=None):
    """Two-panel Figure 4.16-style summary: slope and offset versus Rext.

    Each successfully fitted run is shown individually, so duplicate Rext scans
    remain visible. Uncertainties are ordinary OLS standard errors from the
    residual covariance, not full systematic measurement uncertainties.
    Failed fits are omitted from the points and reported by the caller.
    """
    from matplotlib.figure import Figure
    if figure is None:
        figure = Figure(figsize=(8.5, 7), layout='constrained')
    else:
        figure.clear()
    axes = figure.subplots(2, 1, sharex=True)
    valid = [entry for entry in fits if entry.get('status') == 'ok'
             and all(math.isfinite(float(entry.get(key, float('nan'))))
                     for key in ('rext_ohm', 'slope_ohm', 'offset_v'))]
    for ax, value, uncertainty, ylabel in [
            (axes[0], 'slope_ohm', 'slope_error_ohm', r'$R_{\mathrm{slope}}$ (Ω)'),
            (axes[1], 'offset_v', 'offset_error_v', r'$V_{\mathrm{ofs}}$ (V)')]:
        values = sorted(valid, key=lambda entry: (entry['rext_ohm'], entry.get('run', '')))
        if values:
            x = np.asarray([entry['rext_ohm'] for entry in values], dtype=float)
            y = np.asarray([entry[value] for entry in values], dtype=float)
            sigma = np.asarray([entry.get(uncertainty, float('nan')) for entry in values],
                               dtype=float)
            sigma = np.where(np.isfinite(sigma) & (sigma >= 0), sigma, 0.0)
            ax.errorbar(x, y, yerr=sigma, fmt='o', markersize=5,
                        color='#1f77b4' if value == 'slope_ohm' else '#d62728',
                        capsize=3, linewidth=1.2, linestyle='none')
        else:
            ax.text(0.5, 0.5, 'No successful linear fits',
                    transform=ax.transAxes, ha='center', va='center')
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.3)
    axes[1].set_xlabel(r'$R_{\mathrm{ext}}$ (Ω)')
    axes[0].set_title(title)
    return figure


def save_comparison(figure, fits, destination, runs=None, settings=None,
                    fit_figure=None):
    """Write publication PNG/PDF and fit CSV, never modify source runs."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for extension in ('png', 'pdf'):
        figure.savefig(destination / f'rext_comparison.{extension}',
                       dpi=300, bbox_inches='tight')
    if fit_figure is None and fits:
        fit_figure = plot_fit_parameters(fits)
    if fit_figure is not None:
        for extension in ('png', 'pdf'):
            fit_figure.savefig(destination / f'rext_fit_parameters.{extension}',
                               dpi=300, bbox_inches='tight')
    if runs is not None:
        manifest = {
            'analysis': 'rext_current_sweep_overlay',
            'settings': settings or {},
            'sources': [
                {
                    'directory': str(run.directory),
                    'board': run.board,
                    'rext_ohm': run.rext_ohms,
                    'configuration': run.configuration,
                } for run in runs
            ],
        }
        (destination / 'rext_comparison_manifest.json').write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + '\n',
            encoding='utf-8')
    if fits:
        keys = ['rext_ohm','run','status','slope_ohm','slope_error_ohm',
                'offset_v','offset_error_v','r2','rmse_v','points']
        with (destination / 'rext_fit_results.csv').open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=keys)
            writer.writeheader()
            for fit in fits:
                writer.writerow(fit)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, nargs='?', default=Path('results'))
    parser.add_argument('--board')
    parser.add_argument('--family', help='Substring of normalized configuration family')
    parser.add_argument('--signal', default='Vin')
    parser.add_argument('--direction', choices=('up','down'), default='up')
    parser.add_argument('--fit', action='store_true')
    parser.add_argument('--fit-min-ma', type=float)
    parser.add_argument('--fit-max-ma', type=float)
    parser.add_argument('--fit-max-voltage', type=float, default=2.0)
    parser.add_argument('--max-plot-ma', type=float)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    runs, errors = discover(args.root, return_errors=True)
    groups = families(runs)
    eligible = [(key, group) for key, group in groups.items()
                if (args.board is None or key[0] == args.board)
                and (args.family is None or args.family.casefold() in key[1])]
    if len(eligible) != 1:
        print('Available configuration families:')
        for (board, family), group in groups.items():
            print(f'  {board}: {family} ({len(group)} runs, '
                  f'{len(set(r.rext_ohms for r in group))} Rext values)')
        if errors:
            print(f'Ignored {len(errors)} incompatible run folders.')
        if not eligible:
            parser.error('No matching group; check configurations and root directory')
        parser.error('More than one group matches; specify --board and --family')
    (_, selected) = eligible[0]
    fig, fit_results = compare(
        selected, signal=args.signal, direction=args.direction, fit=args.fit,
        fit_min_ma=args.fit_min_ma, fit_max_ma=args.fit_max_ma,
        fit_max_voltage=args.fit_max_voltage, max_plot_ma=args.max_plot_ma,
    )
    output = args.output or args.root / 'rext_comparisons'
    save_comparison(fig, fit_results, output, selected, {
        'signal': args.signal, 'direction': args.direction, 'fit': args.fit,
        'fit_min_ma': args.fit_min_ma, 'fit_max_ma': args.fit_max_ma,
        'fit_max_voltage': args.fit_max_voltage, 'plot_max_ma': args.max_plot_ma,
    })
    print('Plots saved to:', output)
    for row in fit_results:
        if row['status'] == 'ok':
            print(f"Rext={row['rext_ohm']:g} Ω: slope={row['slope_ohm']:.4g} Ω, "
                  f"Vofs={row['offset_v']:.4g} V, R²={row['r2']:.4g}")
        else:
            print(f"Rext={row['rext_ohm']:g} Ω: {row['status']}")


if __name__ == '__main__':
    main()
