"""Interactive non-destructive Rext sweep comparison for RD53 board runs."""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

# Match the main GUI's process-local workaround for broken host GTK schemas.
# Must be set before importing Qt or matplotlib's Qt backend.
if sys.platform.startswith('linux') and os.environ.get('RD53_NATIVE_QT_THEME') != '1':
    os.environ['QT_QPA_PLATFORMTHEME'] = 'generic'
    os.environ['QT_STYLE_OVERRIDE'] = 'Fusion'

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QPushButton, QComboBox, QFileDialog, QMessageBox,
    QCheckBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QWidget, QSplitter, QPlainTextEdit, QTabWidget,
)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT

from config_parameters import discovered_parameters, normalized_name
from rext_compare import (discover, families, signal_choices, compare,
                          plot_fit_parameters, save_comparison)


class RextCompareWindow(QDialog):
    """Load and compare original data without opening instruments or altering runs."""
    def __init__(self, root='results', parent=None):
        super().__init__(parent)
        self.setWindowTitle('RD53 · Compare configuration sweeps')
        self.resize(1220, 850)
        self._initializing = True
        self.groups = {}
        self.selected_runs = []
        self.last_fit_results = []
        self.last_figure = None

        layout = QVBoxLayout(self)
        heading = QLabel('Compare input-current sweeps by configuration')
        heading.setStyleSheet('font-size:18px;font-weight:700')
        layout.addWidget(heading)
        help_text = QLabel(
            'Choose whether to vary Rext or the configured offset voltage. '
            'Choose ONE voltage to overlay. Only current-sweep runs are included. '
            'Raw CSV files and metadata are never modified.')
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        folder_row = QHBoxLayout()
        self.root = QLineEdit(str(root))
        browse = QPushButton('Browse…')
        browse.clicked.connect(self.browse)
        refresh = QPushButton('Find runs')
        refresh.clicked.connect(self.reload)
        folder_row.addWidget(QLabel('Results folder'))
        folder_row.addWidget(self.root, 1)
        folder_row.addWidget(browse)
        folder_row.addWidget(refresh)
        layout.addLayout(folder_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        side = QWidget()
        options = QVBoxLayout(side)
        options.setContentsMargins(2, 2, 2, 2)
        group_form = QFormLayout()
        self.vary = QComboBox()
        self.vary.addItem('External resistor (Rext)', 'rext')
        self.vary.addItem('Configured offset voltage (Voffset)', 'offset')
        self.vary.setToolTip('Found automatically in saved acquisition configurations; select any numeric parameter to compare.')
        self.vary.currentIndexChanged.connect(self.reload)
        group_form.addRow('Vary parameter', self.vary)
        self.group = QComboBox()
        self.group.currentIndexChanged.connect(self.refresh_group)
        group_form.addRow('Configuration family', self.group)
        self.signal = QComboBox()
        self.signal.currentIndexChanged.connect(self.update_preview)
        group_form.addRow('Measured voltage', self.signal)
        self.direction = QComboBox()
        self.direction.addItems(['up','down'])
        self.direction.currentIndexChanged.connect(self.update_preview)
        group_form.addRow('Direction', self.direction)
        options.addLayout(group_form)

        options.addWidget(QLabel('Included measurements (newest per parameter value selected):'))
        self.runs = QTableWidget(0, 4)
        self.runs.setHorizontalHeaderLabels(['Use', 'Rext [Ω]', 'Run timestamp', 'Run folder'])
        self.runs.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.runs.horizontalHeader().setStretchLastSection(True)
        self.runs.setMinimumHeight(185)
        self.runs.itemChanged.connect(self.update_preview)
        options.addWidget(self.runs)

        plot_form = QFormLayout()
        self.plot_limit = QDoubleSpinBox()
        self.plot_limit.setDecimals(2)
        self.plot_limit.setRange(0.01, 100000)
        self.plot_limit.setValue(250)
        self.plot_limit.setToolTip('Maximum input current shown on the x axis; does not change raw data.')
        self.plot_limit.valueChanged.connect(self.update_preview)
        plot_form.addRow('Plot max current [mA]', self.plot_limit)

        self.fit = QCheckBox('Draw fitted lines on overlay')
        self.fit.setChecked(False)
        self.fit.toggled.connect(self.update_preview)
        plot_form.addRow(self.fit)

        self.fit_start = QDoubleSpinBox()
        self.fit_end = QDoubleSpinBox()
        for box in (self.fit_start, self.fit_end):
            box.setDecimals(2)
            box.setRange(0, 100000)
            box.valueChanged.connect(self.update_preview)
        self.fit_start.setValue(10)
        self.fit_end.setValue(100)
        plot_form.addRow('Fit from [mA]', self.fit_start)
        plot_form.addRow('Fit to [mA]', self.fit_end)

        self.fit_voltage = QDoubleSpinBox()
        self.fit_voltage.setDecimals(3)
        self.fit_voltage.setRange(0.001, 32)
        self.fit_voltage.setValue(2.0)
        self.fit_voltage.valueChanged.connect(self.update_preview)
        plot_form.addRow('Fit points with V ≤ [V]', self.fit_voltage)
        options.addLayout(plot_form)

        caveat = QLabel(
            'Both plots use fits: V = R_slope × I + Vofs. The slope is in Ω (V/A). '
            'Choose an interval where the trace is approximately linear; '
            'the 2 V cutoff alone does NOT identify that region.')
        caveat.setWordWrap(True)
        options.addWidget(caveat)

        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setMaximumHeight(175)
        options.addWidget(self.status)

        actions = QHBoxLayout()
        redraw = QPushButton('Generate overlay')
        redraw.clicked.connect(self.update_preview)
        save = QPushButton('Save both plots + fit CSV…')
        save.clicked.connect(self.save)
        actions.addWidget(redraw)
        actions.addWidget(save)
        options.addLayout(actions)
        splitter.addWidget(side)

        plot_tabs = QTabWidget()
        plot_widget = QWidget()
        plot_layout = QVBoxLayout(plot_widget)
        self.figure = Figure(figsize=(8.5, 6))
        self.canvas = FigureCanvasQTAgg(self.figure)
        plot_layout.addWidget(NavigationToolbar2QT(self.canvas, plot_widget))
        plot_layout.addWidget(self.canvas, 1)
        plot_tabs.addTab(plot_widget, 'Voltage vs input current')

        parameters_widget = QWidget()
        parameters_layout = QVBoxLayout(parameters_widget)
        self.parameters_figure = Figure(figsize=(8.5, 7))
        self.parameters_canvas = FigureCanvasQTAgg(self.parameters_figure)
        parameters_layout.addWidget(NavigationToolbar2QT(self.parameters_canvas, parameters_widget))
        parameters_layout.addWidget(self.parameters_canvas, 1)
        plot_tabs.addTab(parameters_widget, 'Fit slope and offset')
        splitter.addWidget(plot_tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([440, 780])
        self._initializing = False
        self.reload()

    def browse(self):
        chosen = QFileDialog.getExistingDirectory(
            self, 'Choose results root (or one board folder)', self.root.text())
        if chosen:
            self.root.setText(chosen)
            self.reload()

    def reload(self):
        try:
            found = discovered_parameters(self.root.text())
            previous = self.vary.currentData()
            self.vary.blockSignals(True)
            try:
                self.vary.clear()
                self.vary.addItem('External resistor (Rext)', 'rext')
                self.vary.addItem('Configured offset voltage (Voffset)', 'offset')
                for key, label, unit, count in found:
                    if key in ('rext','offset'):
                        continue
                    suffix = f' [{unit}]' if unit else ''
                    self.vary.addItem(f'{label}{suffix} · {count} runs', key)
                index = self.vary.findData(previous)
                self.vary.setCurrentIndex(max(0,index))
            finally:
                self.vary.blockSignals(False)
            scans, errors = discover(self.root.text(), return_errors=True,
                                    parameter=self.vary.currentData())
            self.groups = families(scans)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Cannot search measurements', str(exc))
            return
        self.group.blockSignals(True)
        self.group.clear()
        for key, group in sorted(self.groups.items(), key=lambda item:(item[0][0],item[0][1])):
            board, family, _parameter = key
            distinct = len(set(run.variable_value for run in group))
            self.group.addItem(f'{board} · {distinct} values · {family}', key)
        self.group.blockSignals(False)
        self.status.setPlainText(
            f'Found {len(scans)} compatible board scans in {len(self.groups)} groups. '
            f'Skipped {len(errors)} directories without a matching parameter or current sweep.')
        self.refresh_group()

    def refresh_group(self):
        key = self.group.currentData()
        group = self.groups.get(key, [])
        key_label = self.vary.currentText().split(' · ')[0].split(' [')[0]
        key_unit = group[0].variable_unit if group else (
            'Ω' if self.vary.currentData()=='rext' else 'V' if self.vary.currentData()=='offset' else '')
        heading = f'{key_label} [{key_unit}]' if key_unit else key_label
        self.runs.setHorizontalHeaderLabels(['Use', heading, 'Run timestamp','Run folder'])
        signals = signal_choices(group)
        self.signal.blockSignals(True)
        self.signal.clear()
        self.signal.addItems(signals)
        for index, name in enumerate(signals):
            if name.casefold().replace('_','') == 'vin':
                self.signal.setCurrentIndex(index)
                break
        self.signal.blockSignals(False)

        self.runs.blockSignals(True)
        self.runs.setRowCount(len(group))
        most_recent = {}
        for run in group:
            if run.variable_value not in most_recent or run.mtime > most_recent[run.variable_value].mtime:
                most_recent[run.variable_value] = run
        for index, run in enumerate(group):
            checkbox = QTableWidgetItem()
            checkbox.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            checkbox.setCheckState(Qt.CheckState.Checked if most_recent[run.variable_value] is run
                                   else Qt.CheckState.Unchecked)
            self.runs.setItem(index, 0, checkbox)
            for col, value in enumerate(
                    (f'{run.variable_value:g}', run.run_label, str(run.directory)), start=1):
                item = QTableWidgetItem(value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.runs.setItem(index, col, item)
        self.runs.blockSignals(False)
        self.update_preview()

    def included(self):
        key = self.group.currentData()
        group = self.groups.get(key, [])
        return [run for row, run in enumerate(group)
                if self.runs.item(row, 0) is not None
                and self.runs.item(row, 0).checkState() == Qt.CheckState.Checked]

    def update_preview(self, *unused):
        # Qt may emit valueChanged/currentIndexChanged during widget construction.
        # Never dereference the figure or the run table before both exist.
        if (self._initializing or not hasattr(self, 'figure') or
                not hasattr(self, 'canvas') or not hasattr(self, 'parameters_figure')):
            return
        selected = self.included()
        self.last_figure = None
        self.last_fit_results = []
        if not selected:
            self.figure.clear()
            self.parameters_figure.clear()
            self.canvas.draw_idle()
            self.parameters_canvas.draw_idle()
            self.status.setPlainText('No measurements selected.')
            return
        signal = self.signal.currentText()
        if not signal:
            self.status.setPlainText('No voltage signal is common to all runs in this group.')
            return
        if self.fit_end.value() <= self.fit_start.value():
            self.status.setPlainText('Fit upper current must exceed lower current.')
            return
        try:
            plotted, result = compare(
                selected, signal=signal, direction=self.direction.currentText(),
                fit=True, draw_fit_lines=self.fit.isChecked(),
                fit_min_ma=self.fit_start.value(),
                fit_max_ma=self.fit_end.value(),
                fit_max_voltage=self.fit_voltage.value(),
                max_plot_ma=self.plot_limit.value(), figure=self.figure,
            )
            plot_fit_parameters(result, figure=self.parameters_figure,
                                title=f'{selected[0].board}: {signal} fit parameters vs '
                                      f'{self.vary.currentText().split(" · ")[0]}')
            self.canvas.draw_idle()
            self.parameters_canvas.draw_idle()
            self.last_figure = plotted
            self.last_fit_results = result
            lines = [f'{len(selected)} runs · {len(set(r.variable_value for r in selected))} parameter values',
                     f'Voltage: {signal}; sweep: {self.direction.currentText()}']
            for item in result:
                if item['status'] == 'ok':
                    lines.append(f"{self.fit_label(item)}: slope={item['slope_ohm']:.4g} Ω, "
                                 f"Vofs={item['offset_v']:.4g} V, R²={item['r2']:.4f}; "
                                 f"n={item['points']}")
                else:
                    lines.append(f"{self.fit_label(item)}: {item['status']}")
            self.status.setPlainText('\n'.join(lines))
        except (ValueError, TypeError, KeyError) as exc:
            self.status.setPlainText(str(exc))

    def fit_label(self,item):
        unit = item.get('variable_unit','')
        name = self.vary.currentText().split(' · ')[0].split(' [')[0]
        return f"{name}={item['variable_value']:g}" + (f" {unit}" if unit else '')

    def save(self):
        if self.last_figure is None:
            QMessageBox.information(self, 'No comparison', 'Generate a valid overlay first.')
            return
        chosen = QFileDialog.getExistingDirectory(
            self, 'Save sweep comparison', str(Path(self.root.text()) / f'{normalized_name(self.vary.currentData())}_comparisons'))
        if not chosen:
            return
        folder = Path(chosen)
        prefix = ('rext' if self.vary.currentData()=='rext'
                  else 'offset_setting' if self.vary.currentData()=='offset'
                  else normalized_name(self.vary.currentData()))
        targets = [folder / f'{name}.{ext}'
                   for name in (f'{prefix}_comparison', f'{prefix}_fit_parameters')
                   for ext in ('png', 'pdf')]
        if any(target.exists() for target in targets):
            reply = QMessageBox.question(
                self, 'Overwrite plots?',
                'Existing comparison PNG/PDF will be overwritten. Continue?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        try:
            save_comparison(self.last_figure, self.last_fit_results, folder, self.included(), {
                'vary': self.vary.currentData(),
                'signal': self.signal.currentText(),
                'direction': self.direction.currentText(),
                'fit': True, 'draw_fit_lines': self.fit.isChecked(),
                'fit_min_ma': self.fit_start.value(),
                'fit_max_ma': self.fit_end.value(),
                'fit_max_voltage': self.fit_voltage.value(),
                'plot_max_ma': self.plot_limit.value(),
            }, fit_figure=self.parameters_figure)
            QMessageBox.information(self, 'Sweep comparison saved', str(folder))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Could not save comparison', str(exc))


def main():
    import sys
    from PyQt6.QtWidgets import QApplication
    owned_app = QApplication.instance() is None
    app = QApplication.instance() or QApplication(sys.argv)
    root = sys.argv[1] if len(sys.argv) > 1 else 'results'
    window = RextCompareWindow(root)
    window.show()
    if owned_app:
        sys.exit(app.exec())


if __name__ == '__main__':
    main()
