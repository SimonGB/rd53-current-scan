"""Non-destructive saved-run plot editor, independent of acquisition and wiring."""
import math
import json
from pathlib import Path
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
                            QLabel, QLineEdit, QDoubleSpinBox, QCheckBox,
                            QComboBox, QTabWidget, QPushButton, QFileDialog,
                            QMessageBox, QScrollArea, QWidget,
                            QTableWidget, QTableWidgetItem, QHeaderView)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QBrush
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from sweep_plots import load_run, draw_sweep, export_sweeps, sweep_mode
from plotting_editor import edit_style, STYLE_KEYS


class HistoryViewer(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.rows, settings, self.directory = load_run(path)
        self.setWindowTitle('Saved measurements · ' + self.directory.name)
        self.resize(1100, 850)
        layout = QVBoxLayout(self)
        source = QLabel(str(self.directory));source.setWordWrap(True);layout.addWidget(source)
        controls = QFormLayout();layout.addLayout(controls)
        self.title = QLineEdit(str(settings.get('plot_title', 'RD53b current scan')))
        controls.addRow('Plot title', self.title)
        self.configuration = QLineEdit(str(settings.get('plot_configuration', '')))
        controls.addRow('Configuration', self.configuration)
        options = QHBoxLayout()
        self.show_configuration = QCheckBox('Show configuration box')
        self.show_configuration.setChecked(not settings.get('plot_hide_configuration', False))
        self.configuration.setEnabled(self.show_configuration.isChecked())
        self.show_configuration.toggled.connect(self.configuration.setEnabled)
        options.addWidget(self.show_configuration)
        self.show_difference = QCheckBox('Show Vin − Vout subplot')
        self.show_difference.setChecked(not settings.get('plot_hide_difference', False))
        options.addWidget(self.show_difference)
        controls.addRow(options)
        self.maximum = QDoubleSpinBox();self.maximum.setDecimals(3)
        self.maximum.setRange(.001, 10000)
        self.mode=sweep_mode(self.rows)
        maximum = float(settings.get('plot_max_v',2.0) if self.mode=='voltage' else settings.get('plot_max_ma',100))
        self.maximum.setValue(maximum if math.isfinite(maximum) and maximum > 0 else 100)
        controls.addRow('Plot maximum [V]' if self.mode=='voltage' else 'Plot maximum [mA]', self.maximum)
        self.board = QComboBox();self.board.addItem('All boards', None)
        for board in dict.fromkeys(r['board'] for r in self.rows):
            self.board.addItem(board, board)
        controls.addRow('Board', self.board)
        self.plotting = QTableWidget(0, 4)
        self.plotting.setHorizontalHeaderLabels(['Board', 'Testpoint', 'Unit', 'Plotting'])
        self.plotting.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.plotting.setMinimumHeight(95)
        self.plotting.setMaximumHeight(185)
        self.plotting.setToolTip('Double-click a Plotting cell to edit color, line and marker.')
        layout.addWidget(QLabel('Testpoint plotting — double-click a Plotting cell:'))
        layout.addWidget(self.plotting)
        self.plotting.cellDoubleClicked.connect(self.edit_plotting)
        self.refresh_style_table()
        explanation = QLabel('Vin − Vout is the regulator voltage drop, not shunted current. '
                             'Only paired local voltage measurements at the same scan point are subtracted.')
        explanation.setWordWrap(True);layout.addWidget(explanation)
        tabs = QTabWidget();layout.addWidget(tabs, 1)
        self.plots = []
        for direction in ('up', 'down'):
            page = QWidget();page_layout = QVBoxLayout(page)
            figure = Figure(figsize=(8, 7), facecolor='#101f2e')
            canvas = FigureCanvasQTAgg(figure)
            page_layout.addWidget(NavigationToolbar2QT(canvas, self))
            scroll = QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(canvas)
            page_layout.addWidget(scroll)
            tabs.addTab(page, direction.capitalize() + ' sweep')
            self.plots.append((direction, figure, canvas))
        export = QPushButton('Save Plots…');export.clicked.connect(self.export)
        layout.addWidget(export)
        for field in (self.title, self.configuration):
            field.textChanged.connect(self.redraw)
        for field in (self.show_configuration, self.show_difference):
            field.toggled.connect(self.redraw)
        self.maximum.valueChanged.connect(self.redraw)
        self.board.currentIndexChanged.connect(self.redraw)
        self.redraw()

    def refresh_style_table(self):
        """One style row per signal, shared across both sweep directions."""
        keys = list(dict.fromkeys((r['board'], r['testpoint'], r['unit']) for r in self.rows))
        self.style_targets = keys
        self.plotting.setRowCount(len(keys))
        for index, key in enumerate(keys):
            sample = next(r for r in self.rows if (r['board'], r['testpoint'], r['unit']) == key)
            for col, value in enumerate(key):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.plotting.setItem(index, col, item)
            style = {name: sample.get(name, '') or '' for name in STYLE_KEYS}
            item = QTableWidgetItem(' · '.join(v for v in style.values() if v) or 'Auto')
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setToolTip('Double-click to change color, line pattern and symbol.')
            if style['color'] and QColor(style['color']).isValid():
                item.setBackground(QBrush(QColor(style['color'])))
            self.plotting.setItem(index, 3, item)

    def edit_plotting(self, row, col):
        if col != 3:
            return
        key = self.style_targets[row]
        sample = next(r for r in self.rows
                      if (r['board'], r['testpoint'], r['unit']) == key)
        previous = {name: sample.get(name, '') or '' for name in STYLE_KEYS}
        selected = edit_style(self, f'{key[0]} / {key[1]} ({key[2]})', previous)
        if selected is None:
            return
        for record in self.rows:
            if (record['board'], record['testpoint'], record['unit']) == key:
                record.update(selected)
        self.refresh_style_table()
        self.redraw()

    def presentation(self):
        """Non-destructive plot settings to save next to the exported figures."""
        styles = []
        for key in self.style_targets:
            sample = next(r for r in self.rows
                          if (r['board'], r['testpoint'], r['unit']) == key)
            styles.append(dict(board=key[0], testpoint=key[1], unit=key[2],
                               **{name: sample.get(name, '') or '' for name in STYLE_KEYS}))
        return {
            'version': 1,
            'settings': {
                'plot_title': self.title.text(),
                'plot_configuration': self.configuration.text(),
                'plot_hide_configuration': not self.show_configuration.isChecked(),
                'plot_hide_difference': not self.show_difference.isChecked(),
                'plot_max_v' if self.mode == 'voltage' else 'plot_max_ma': self.maximum.value(),
            },
            'styles': styles,
        }

    def visible_rows(self):
        board = self.board.currentData()
        return self.rows if board is None else [r for r in self.rows if r['board'] == board]

    def note(self):
        return self.configuration.text() if self.show_configuration.isChecked() else ''

    def redraw(self):
        rows = self.visible_rows()
        for direction, figure, canvas in self.plots:
            units = len(set(r['unit'] for r in rows if r['direction'] == direction)) or 1
            canvas.setMinimumHeight(300 * (units + self.show_difference.isChecked()))
            draw_sweep(figure, rows, direction, self.title.text(), self.maximum.value(),
                       color_rows=self.rows, configuration=self.note(), dark=True,
                       show_difference=self.show_difference.isChecked())
            canvas.draw_idle()

    def export(self):
        path = QFileDialog.getExistingDirectory(self, 'Save PDF/PNG plots', str(self.directory))
        if not path:
            return
        from pathlib import Path
        output = Path(path)
        if any((output / f'mapped_voltages_{d}.{e}').exists()
               for d in ('up', 'down') for e in ('pdf', 'png')):
            answer = QMessageBox.question(self, 'Replace plots?',
                'This folder already contains sweep plots. Replace files with the same names?')
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            export_sweeps(self.visible_rows(), output, self.title.text(),
                          self.maximum.value(), self.note(),
                          show_difference=self.show_difference.isChecked(), color_rows=self.rows)
            # A sidecar preserves edits without rewriting measurement CSV or metadata.
            (output / 'plot_presentation.json').write_text(
                json.dumps(self.presentation(), indent=2, ensure_ascii=False) + '\n',
                encoding='utf-8')
            QMessageBox.information(self, 'Plots exported', str(output))
        except Exception as exc:
            QMessageBox.warning(self, 'Export failed', str(exc))
