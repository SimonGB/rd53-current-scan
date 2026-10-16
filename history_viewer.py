"""Read-only saved-run plot window, independent of acquisition and wiring."""
import math
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
                            QLabel, QLineEdit, QDoubleSpinBox, QCheckBox,
                            QComboBox, QTabWidget, QPushButton, QFileDialog,
                            QMessageBox, QScrollArea, QWidget)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from sweep_plots import load_run, draw_sweep, export_sweeps


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
        maximum = float(settings.get('plot_max_ma', 100))
        self.maximum.setValue(maximum if math.isfinite(maximum) and maximum > 0 else 100)
        controls.addRow('Plot maximum [mA]', self.maximum)
        self.board = QComboBox();self.board.addItem('All boards', None)
        for board in dict.fromkeys(r['board'] for r in self.rows):
            self.board.addItem(board, board)
        controls.addRow('Board', self.board)
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
        export = QPushButton('Export displayed plots…');export.clicked.connect(self.export)
        layout.addWidget(export)
        for field in (self.title, self.configuration):
            field.textChanged.connect(self.redraw)
        for field in (self.show_configuration, self.show_difference):
            field.toggled.connect(self.redraw)
        self.maximum.valueChanged.connect(self.redraw)
        self.board.currentIndexChanged.connect(self.redraw)
        self.redraw()

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
        path = QFileDialog.getExistingDirectory(self, 'Export PDF/PNG plots', str(self.directory))
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
            QMessageBox.information(self, 'Plots exported', str(output))
        except Exception as exc:
            QMessageBox.warning(self, 'Export failed', str(exc))
