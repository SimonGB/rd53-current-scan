"""Interactive non-destructive Rext sweep comparison for RD53 board runs."""
from __future__ import annotations

import math
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QPushButton, QComboBox, QFileDialog, QMessageBox,
    QCheckBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QWidget, QSplitter, QPlainTextEdit,
)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT

from rext_compare import discover, families, signal_choices, compare, save_comparison


class RextCompareWindow(QDialog):
    """Load and compare original data without opening instruments or altering runs."""
    def __init__(self, root='results', parent=None):
        super().__init__(parent)
        self.setWindowTitle('RD53 · Compare Rext current sweeps')
        self.resize(1220, 850)
        self.groups = {}
        self.selected_runs = []
        self.last_fit_results = []
        self.last_figure = None

        layout = QVBoxLayout(self)
        heading = QLabel('Compare input-current sweeps by external resistor')
        heading.setStyleSheet('font-size:18px;font-weight:700')
        layout.addWidget(heading)
        help_text = QLabel(
            'Groups scans with the same recorded configuration except Rext. '
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

        options.addWidget(QLabel('Included measurements (newest scan per Rext selected):'))
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

        self.fit = QCheckBox('Overlay linear fits (selected interval only)')
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
            'Fit model: V = R_slope × I + Vofs. The slope is reported in Ω (V/A). '
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
        save = QPushButton('Save PNG/PDF + fit CSV…')
        save.clicked.connect(self.save)
        actions.addWidget(redraw)
        actions.addWidget(save)
        options.addLayout(actions)
        splitter.addWidget(side)

        plot_widget = QWidget()
        plot_layout = QVBoxLayout(plot_widget)
        self.figure = Figure(figsize=(8.5, 6))
        self.canvas = FigureCanvasQTAgg(self.figure)
        plot_layout.addWidget(NavigationToolbar2QT(self.canvas, plot_widget))
        plot_layout.addWidget(self.canvas, 1)
        splitter.addWidget(plot_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([440, 780])
        self.reload()

    def browse(self):
        chosen = QFileDialog.getExistingDirectory(
            self, 'Choose results root (or one board folder)', self.root.text())
        if chosen:
            self.root.setText(chosen)
            self.reload()

    def reload(self):
        try:
            scans, errors = discover(self.root.text(), return_errors=True)
            self.groups = families(scans)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Cannot search measurements', str(exc))
            return
        self.group.blockSignals(True)
        self.group.clear()
        for key, group in sorted(self.groups.items(), key=lambda item:(item[0][0],item[0][1])):
            board, family = key
            distinct = len(set(run.rext_ohms for run in group))
            self.group.addItem(f'{board} · {distinct} Rext values · {family}', key)
        self.group.blockSignals(False)
        self.status.setPlainText(
            f'Found {len(scans)} compatible board scans in {len(self.groups)} groups. '
            f'Skipped {len(errors)} directories without a usable current-sweep Rext configuration.')
        self.refresh_group()

    def refresh_group(self):
        key = self.group.currentData()
        group = self.groups.get(key, [])
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
            if run.rext_ohms not in most_recent or run.mtime > most_recent[run.rext_ohms].mtime:
                most_recent[run.rext_ohms] = run
        for index, run in enumerate(group):
            checkbox = QTableWidgetItem()
            checkbox.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            checkbox.setCheckState(Qt.CheckState.Checked if most_recent[run.rext_ohms] is run
                                   else Qt.CheckState.Unchecked)
            self.runs.setItem(index, 0, checkbox)
            for col, value in enumerate(
                    (f'{run.rext_ohms:g}', run.run_label, str(run.directory)), start=1):
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
        selected = self.included()
        self.last_figure = None
        if not selected:
            self.figure.clear()
            self.canvas.draw_idle()
            self.status.setPlainText('No measurements selected.')
            return
        signal = self.signal.currentText()
        if not signal:
            self.status.setPlainText('No voltage signal is common to all runs in this group.')
            return
        if self.fit.isChecked() and self.fit_end.value() <= self.fit_start.value():
            self.status.setPlainText('Fit upper current must exceed lower current.')
            return
        try:
            plotted, result = compare(
                selected, signal=signal, direction=self.direction.currentText(),
                fit=self.fit.isChecked(),
                fit_min_ma=self.fit_start.value() if self.fit.isChecked() else None,
                fit_max_ma=self.fit_end.value() if self.fit.isChecked() else None,
                fit_max_voltage=self.fit_voltage.value(),
                max_plot_ma=self.plot_limit.value(), figure=self.figure,
            )
            self.canvas.draw_idle()
            self.last_figure = plotted
            self.last_fit_results = result
            lines = [f'{len(selected)} runs · {len(set(r.rext_ohms for r in selected))} Rext values',
                     f'Voltage: {signal}; sweep: {self.direction.currentText()}']
            for item in result:
                if item['status'] == 'ok':
                    lines.append(f"Rext={item['rext_ohm']:g} Ω: slope={item['slope_ohm']:.4g} Ω, "
                                 f"Vofs={item['offset_v']:.4g} V, R²={item['r2']:.4f}; "
                                 f"n={item['points']}")
                else:
                    lines.append(f"Rext={item['rext_ohm']:g} Ω: {item['status']}")
            self.status.setPlainText('\n'.join(lines))
        except (ValueError, TypeError, KeyError) as exc:
            self.status.setPlainText(str(exc))

    def save(self):
        if self.last_figure is None:
            QMessageBox.information(self, 'No comparison', 'Generate a valid overlay first.')
            return
        chosen = QFileDialog.getExistingDirectory(
            self, 'Save Rext comparison', str(Path(self.root.text()) / 'rext_comparisons'))
        if not chosen:
            return
        folder = Path(chosen)
        targets = [folder / f'rext_comparison.{ext}' for ext in ('png', 'pdf')]
        if any(target.exists() for target in targets):
            reply = QMessageBox.question(
                self, 'Overwrite plots?',
                'Existing Rext comparison PNG/PDF will be overwritten. Continue?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        try:
            save_comparison(self.last_figure, self.last_fit_results, folder)
            QMessageBox.information(self, 'Rext comparison saved', str(folder))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Could not save comparison', str(exc))


def main():
    import sys
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    root = sys.argv[1] if len(sys.argv) > 1 else 'results'
    window = RextCompareWindow(root)
    window.show()
    if QApplication.instance() is app:
        sys.exit(app.exec())


if __name__ == '__main__':
    main()
