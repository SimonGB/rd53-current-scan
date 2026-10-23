"""Persistent instrument setup and live acquisition dashboard for RD53 station.

These views mirror existing controls instead of reparenting the proven board
editor or Hameg scan widgets. They share the existing instrument/mapping state.
"""
from __future__ import annotations

import math
from PyQt6.QtWidgets import (
    QWidget,QVBoxLayout,QFormLayout,QGroupBox,QHBoxLayout,QLabel,QPushButton,
    QComboBox,QLineEdit,QSpinBox,QProgressBar,QPlainTextEdit,QTabWidget,
)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg


class InstrumentPage(QWidget):
    """One shared read/write entry point for instrument connections.

    Existing Hameg setup fields remain in the legacy scan form and are
    two-way synchronized rather than copied into a second configuration.
    """
    def __init__(self,station,rigol_page):
        super().__init__(station)
        self.station=station
        self.rigol=rigol_page
        layout=QVBoxLayout(self)
        heading=QLabel('1  ·  Instruments')
        heading.setStyleSheet('font-size:22px;font-weight:700')
        layout.addWidget(heading)
        desc=QLabel('Configure the connected instruments here, then assign their channels '
                    'to physical board testpoints on the next tab. '
                    'These settings are shared with the acquisition pages.')
        desc.setWordWrap(True)
        layout.addWidget(desc)

        hmp_box=QGroupBox('Hameg HMP4040 · DC supply')
        hmp_form=QFormLayout(hmp_box)
        self.hmp_port=QLineEdit(station.port.text())
        self.hmp_channel=QSpinBox()
        self.hmp_channel.setRange(1,4)
        self.hmp_channel.setValue(station.hmp.value())
        hmp_form.addRow('Serial device',self.hmp_port)
        hmp_form.addRow('Output channel',self.hmp_channel)
        hmp_form.addRow(QLabel('DC setpoints and compliance limits are configured '
                               'within the DC scan mode. The Rigol sweep does not '
                               'change the Hameg output.'))
        layout.addWidget(hmp_box)

        reader_box=QGroupBox('Readout · PicoScope / Venelin')
        reader_form=QFormLayout(reader_box)
        self.reader=QComboBox()
        self.reader.addItems(['pico','venelin'])
        self.reader.setCurrentText(station.backend.currentText())
        reader_form.addRow('Readout source',self.reader)
        reader_form.addRow(QLabel('PicoScope A–D share the oscilloscope ground. '
                                  'Do not attach grounded probes across floating '
                                  'serial domains. Physical pinning is configured '
                                  'on the Boards & pinning tab.'))
        layout.addWidget(reader_box)

        rigol_box=QGroupBox('Rigol DG4162 · modulation source')
        rigol_form=QFormLayout(rigol_box)
        self.rigol_resource=QLineEdit(rigol_page.resource.currentText())
        rigol_form.addRow('USBTMC / VISA resource',self.rigol_resource)
        identify=QPushButton('Identify connected Rigol (output unchanged)')
        identify.clicked.connect(rigol_page.identify)
        rigol_form.addRow(identify)
        rigol_form.addRow(QLabel('Frequency, amplitude and output arming are '
                                 'configured in Measurements, not here.'))
        layout.addWidget(rigol_box)

        self.hmp_port.textChanged.connect(station.port.setText)
        station.port.textChanged.connect(self.hmp_port.setText)
        self.hmp_channel.valueChanged.connect(station.hmp.setValue)
        station.hmp.valueChanged.connect(self.hmp_channel.setValue)
        self.reader.currentTextChanged.connect(station.backend.setCurrentText)
        station.backend.currentTextChanged.connect(self.reader.setCurrentText)
        self.rigol_resource.textChanged.connect(rigol_page.resource.setEditText)
        rigol_page.resource.currentTextChanged.connect(self.rigol_resource.setText)
        layout.addStretch()


class LiveDashboard(QWidget):
    """Run progress and live graphs, independent of the configuration widgets."""
    def __init__(self,parent=None):
        super().__init__(parent)
        layout=QVBoxLayout(self)
        title=QLabel('4  ·  Live acquisition')
        title.setStyleSheet('font-size:22px;font-weight:700')
        layout.addWidget(title)
        run_row=QHBoxLayout()
        self.status=QLabel('READY · no acquisition running')
        self.status.setStyleSheet('font-weight:bold')
        self.progress=QProgressBar()
        self.progress.setRange(0,100)
        self.progress.setValue(0)
        self.progress.setFormat('%p%')
        run_row.addWidget(self.status,1)
        run_row.addWidget(self.progress,2)
        layout.addLayout(run_row)

        self.mode_tabs=QTabWidget()
        self.dg_figure=Figure(figsize=(9,5),layout='constrained')
        self.dg_canvas=FigureCanvasQTAgg(self.dg_figure)
        self.mode_tabs.addTab(self.dg_canvas,'Rigol frequency response')
        self.dc_figure=Figure(figsize=(9,5),layout='constrained')
        self.dc_canvas=FigureCanvasQTAgg(self.dc_figure)
        self.mode_tabs.addTab(self.dc_canvas,'Hameg line/current regulation')
        layout.addWidget(self.mode_tabs,1)
        self.plot_quantity=QComboBox()
        self.plot_quantity.addItems([
            'VIN→VOUT attenuation [dB]','VOUT relative phase [deg]',
            'Mean voltage','Peak-to-peak voltage','Standard deviation'])
        self.plot_quantity.currentIndexChanged.connect(self.redraw_dg)
        option_row=QHBoxLayout()
        option_row.addWidget(QLabel('Rigol plot'))
        option_row.addWidget(self.plot_quantity)
        option_row.addStretch()
        layout.addLayout(option_row)
        self.notes=QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(125)
        layout.addWidget(self.notes)
        self.dg_rows=[]
        self.dg_spacing='log'
        self.dc_rows=[]

    def clear_dg(self):
        self.dg_rows=[]
        self.progress.setValue(0)
        self.dg_figure.clear()
        self.dg_canvas.draw_idle()
        self.mode_tabs.setCurrentIndex(0)

    def start_dg(self):
        self.clear_dg()
        self.status.setText('DG4162 · connecting to instruments')
        self.notes.appendPlainText('DG4162 sweep started')

    def update_dg(self,row,total):
        try:
            if row.get('_phase')=='settling':
                self.mode_tabs.setCurrentIndex(0)
                self.status.setText(
                    f"DG4162 · settling {row['index']+1}/{total} · "
                    f"{row['readback_hz']:.5g} Hz · hold {row['dwell_s']:.1f} s")
                self.progress.setValue(round(100*row['index']/total))
                return
            self.dg_rows.append(row)
            self.progress.setValue(round(100*(row['index']+1)/total))
            self.status.setText(f"DG4162 · point {row['index']+1}/{total} · "
                                f"{row['readback_hz']:.5g} Hz")
            self.notes.appendPlainText(
                f"{row['index']+1}/{total} · {row['readback_hz']:.6g} Hz")
            self.redraw_dg()
        except Exception as error:
            # Never let a Qt slot exception terminate the entire experiment.
            self.notes.appendPlainText('Live plot warning: '+str(error))

    def redraw_dg(self,*unused):
        if not self.dg_rows:
            return
        choice=self.plot_quantity.currentIndex()
        suffix=('_attenuation_db','_phase_deg','_mean_v','_vpp_v','_std_v')[choice]
        labels=sorted({key for row in self.dg_rows for key in row if key.endswith(suffix)})
        self.dg_figure.clear()
        ax=self.dg_figure.subplots()
        xs=[row['readback_hz'] for row in self.dg_rows]
        plotted=False
        for key in labels:
            ys=[row.get(key,float('nan')) for row in self.dg_rows]
            if any(isinstance(x,(int,float)) and math.isfinite(x) for x in ys):
                plotted=True
            ax.plot(xs,ys,'o-',label=key[:-len(suffix)].replace('_',' / '))
        if self.dg_spacing=='log':
            ax.set_xscale('log')
        ax.set_xlabel('Frequency [Hz]')
        ax.set_ylabel(self.plot_quantity.currentText())
        ax.set_title('Input-referenced frequency response')
        ax.grid(alpha=.3)
        if plotted:
            ax.legend(fontsize=8)
        else:
            ax.text(.5,.5,'No valid response points yet. Check Vin/Vout mappings '
                    'and scope window length.',transform=ax.transAxes,
                    ha='center',va='center',wrap=True)
        self.dg_canvas.draw_idle()

    def start_dc(self):
        self.dc_rows=[]
        self.dc_figure.clear()
        self.dc_canvas.draw_idle()
        self.mode_tabs.setCurrentIndex(1)
        self.progress.setValue(0)
        self.status.setText('Hameg · starting scan')

    def update_dc(self,rows,current,total):
        self.dc_rows=list(rows)
        self.progress.setValue(round(100*current/max(1,total)))
        self.status.setText(f'Hameg · point {current}/{total}')
        try:
            ax=self.dc_figure.subplots() if not self.dc_figure.axes else self.dc_figure.axes[0]
            ax.clear()
            for board,point in dict.fromkeys((r['board'],r['testpoint']) for r in rows):
                subset=[r for r in rows if r['board']==board and r['testpoint']==point]
                xs=[r.get('voltage_measured_V',r.get('supply_voltage_V',0))
                    if r.get('sweep_mode')=='voltage' else r.get('current_measured_A',0)*1000
                    for r in subset]
                ys=[r['mean'] for r in subset]
                ax.plot(xs,ys,'o-',label=f'{board} / {point}')
            ax.set(xlabel='Measured Hameg voltage [V]' if rows and rows[0].get('sweep_mode')=='voltage'
                   else 'Measured Hameg current [mA]',ylabel='Mapped signal')
            ax.grid(alpha=.3)
            if rows:
                ax.legend(fontsize=8)
            self.dc_canvas.draw_idle()
        except Exception as error:
            self.notes.appendPlainText('DC plot warning: '+str(error))

    def finished(self,text,success):
        self.status.setText('COMPLETE' if success else 'STOPPED / ERROR')
        self.notes.appendPlainText(text)
