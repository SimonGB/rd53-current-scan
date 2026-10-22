"""DG4162 generator-only sweep page embedded in the shared RD53 station."""
from __future__ import annotations

import argparse
from pathlib import Path
import threading

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QGroupBox,QLineEdit,QComboBox,
    QDoubleSpinBox,QSpinBox,QPushButton,QLabel,QCheckBox,QPlainTextEdit,
    QProgressBar,QMessageBox,
)
from dg4162_frequency_sweep import run, validate, frequency_points, make_parser


class DGWorker(QThread):
    progress = pyqtSignal(object, int)
    completed = pyqtSignal(str, bool)

    def __init__(self, args, profile=None):
        super().__init__()
        self.args = args
        self.profile = profile
        self.cancel = threading.Event()

    def run(self):
        try:
            result = run(self.args, stop_event=self.cancel, on_step=self.progress.emit,
                         profile=self.profile)
            self.completed.emit(str(result), True)
        except BaseException as error:
            self.completed.emit(f'{type(error).__name__}: {error}', False)


class DG4162Page(QWidget):
    """Dedicated sweep mode. No output is enabled unless explicitly checked."""
    def __init__(self, parent=None, profile_provider=None):
        super().__init__(parent)
        self.worker = None
        self.profile_provider=profile_provider
        self.live_points=[]
        layout=QVBoxLayout(self)
        header=QLabel('DG4162 · Frequency response / stimulus')
        header.setStyleSheet('font-size:20px;font-weight:bold')
        layout.addWidget(header)
        warning=QLabel(
            'The Rigol is a 50 Ω VOLTAGE generator, not a current source. '
            'Do not connect it directly in parallel with an energized Hameg '
            'or to floating serial-power domains. Verify your interface and '
            'ground references before enabling output.')
        warning.setWordWrap(True)
        layout.addWidget(warning)

        card=QGroupBox('Generator & waveform')
        form=QFormLayout(card)
        self.resource=QComboBox()
        self.resource.setEditable(True)
        self.resource.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.resource.setMinimumWidth(380)
        form.addRow('VISA resource',self.resource)
        discover=QPushButton('Find USBTMC / VISA resources')
        discover.clicked.connect(self.find_resources)
        identify=QPushButton('Identify')
        identify.clicked.connect(self.identify)
        resource_buttons=QHBoxLayout()
        resource_buttons.addWidget(discover)
        resource_buttons.addWidget(identify)
        form.addRow(resource_buttons)

        self.channel=QSpinBox()
        self.channel.setRange(1,2)
        form.addRow('Output channel',self.channel)
        self.wave=QComboBox()
        self.wave.addItems(['square','sine'])
        form.addRow('Waveform',self.wave)

        self.numeric={}
        for key,title,val,minvalue,maxvalue,decimals in [
            ('start_hz','Start frequency [Hz]',1,0.001,50e6,3),
            ('stop_hz','Stop frequency [Hz]',10000,0.001,50e6,3),
            ('amplitude_vpp','High-Z calibrated amplitude [Vpp]',0.2,0.001,2,4),
            ('offset_v','DC offset [V]',0,-2,2,4),
            ('cycles','Cycles per frequency',20,1,1e7,2),
            ('min_dwell_s','Minimum dwell [s]',.2,.01,120,3),
            ('max_dwell_s','Maximum dwell [s]',30,.01,120,3),
        ]:
            box=QDoubleSpinBox()
            box.setRange(minvalue,maxvalue)
            box.setDecimals(decimals)
            box.setValue(val)
            self.numeric[key]=box
            form.addRow(title,box)
        self.points=QSpinBox()
        self.points.setRange(2,1000)
        self.points.setValue(25)
        form.addRow('Frequency points',self.points)
        self.spacing=QComboBox()
        self.spacing.addItems(['log','linear'])
        form.addRow('Frequency spacing',self.spacing)
        layout.addWidget(card)

        options=QGroupBox('Safety and acquisition')
        opts=QFormLayout(options)
        self.enable=QCheckBox('Enable DG4162 output (unchecked = identify/preview only)')
        self.confirm=QCheckBox('I verified electrical interface, current limiting and grounds')
        opts.addRow(self.enable)
        opts.addRow(self.confirm)
        self.enable.toggled.connect(self.confirm.setEnabled)
        self.confirm.setEnabled(False)
        self.output=QLineEdit('results/dg4162_frequency_sweep')
        opts.addRow('Result directory',self.output)
        self.measure=QCheckBox('Capture PicoScope voltages using shared Setup mappings')
        self.measure.setChecked(profile_provider is not None)
        opts.addRow(self.measure)
        self.mapping_status=QLabel('Configure the PicoScope channels in Setup tab')
        self.mapping_status.setWordWrap(True)
        opts.addRow(self.mapping_status)
        note=QLabel('Hameg DC current is not changed here. Each frequency gets one '
                    'short PicoScope waveform capture. At low frequencies '
                    'the capture can miss most of a cycle.')
        note.setWordWrap(True)
        opts.addRow(note)
        layout.addWidget(options)

        buttons=QHBoxLayout()
        self.preview=QPushButton('Preview frequencies')
        self.preview.clicked.connect(self.preview_plan)
        self.start=QPushButton('▶ Run frequency sweep')
        self.start.clicked.connect(self.start_run)
        self.stop=QPushButton('■ Stop / output off')
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.stop_run)
        buttons.addWidget(self.preview)
        buttons.addWidget(self.start)
        buttons.addWidget(self.stop)
        layout.addLayout(buttons)
        self.progress=QProgressBar()
        layout.addWidget(self.progress)
        self.figure=Figure(figsize=(8,4),layout='constrained')
        self.canvas=FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(230)
        layout.addWidget(self.canvas,2)
        self.log=QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
        layout.addWidget(self.log,1)
        self.enable.toggled.connect(self.update_start_text)

    def update_start_text(self):
        self.start.setText('▶ Run frequency sweep' if self.enable.isChecked()
                           else 'Preview instrument (no output)')

    def collect(self, identify=False):
        args=make_parser().parse_args([])
        args.resource=self.resource.currentText().strip() or None
        args.channel=self.channel.value()
        args.wave=self.wave.currentText()
        args.points=self.points.value()
        args.spacing=self.spacing.currentText()
        args.output=Path(self.output.text()).expanduser()
        for key,box in self.numeric.items():
            setattr(args,key,box.value())
        args.enable_output=self.enable.isChecked() and not identify
        args.confirm_safe_interface=self.confirm.isChecked() and not identify
        args.identify=identify
        args.list_resources=False
        args.simulate=False
        return args

    def find_resources(self):
        try:
            # Include Linux kernel USBTMC devices even when VISA/libusb
            # discovery cannot enumerate the connected Rigol.
            from dg4162_frequency_sweep import discover_resources
            resources=discover_resources()
            previous=self.resource.currentText()
            self.resource.clear()
            self.resource.addItems(resources)
            if previous:
                self.resource.setEditText(previous)
            self.log.appendPlainText('VISA resources: '+(', '.join(resources) if resources else 'none found'))
        except Exception as error:
            QMessageBox.warning(self,'Instrument discovery failed',str(error))

    def identify(self):
        if self.worker and self.worker.isRunning():
            return
        try:
            args=self.collect(identify=True)
            self.launch(args)
        except Exception as error:
            QMessageBox.warning(self,'DG4162 identification',str(error))

    def preview_plan(self):
        try:
            args=self.collect()
            frequencies=frequency_points(args.start_hz,args.stop_hz,args.points,args.spacing)
            # Preview never needs a VISA connection or an enabled output.
            # Validate the numeric sweep settings with a synthetic resource.
            args.resource=args.resource or 'PREVIEW_ONLY'
            args.enable_output=False
            args.confirm_safe_interface=False
            validate(args)
            self.progress.setRange(0,len(frequencies))
            self.progress.setValue(0)
            sample=frequencies[:6]
            self.log.appendPlainText(
                f"Preview {len(frequencies)} {args.spacing} frequency steps: "
                + ', '.join(f'{x:g} Hz' for x in sample)
                + (' …' if len(frequencies)>6 else ''))
        except Exception as error:
            QMessageBox.warning(self,'Invalid sweep settings',str(error))

    def start_run(self):
        if self.worker and self.worker.isRunning():
            return
        try:
            args=self.collect()
            validate(args)
            if args.enable_output:
                answer=QMessageBox.question(
                    self,'Enable waveform output?',
                    'Confirmed connection and safe waveform levels? The DG4162 '
                    'will emit a waveform. Verify Hameg and PicoScope ground references.',
                    QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if answer!=QMessageBox.StandardButton.Yes:
                    return
            self.launch(args)
        except Exception as error:
            QMessageBox.warning(self,'Cannot start DG4162 sweep',str(error))

    def resolve_profile(self):
        if not self.measure.isChecked():
            return None
        if self.profile_provider is None:
            raise ValueError('Shared PicoScope mapping is unavailable')
        from mapping import validate_profile
        profile=validate_profile(self.profile_provider())
        if profile['backend'] != 'pico':
            raise ValueError('Select Pico as the measurement source in Setup')
        self.mapping_status.setText('Measuring: ' + ', '.join(
            f"{m['board']}/{m['testpoint']} → {m['channel']}" for m in profile['mappings']))
        return profile

    def launch(self,args):
        profile=self.resolve_profile() if args.enable_output and not args.identify else None
        self.live_points=[]
        self.figure.clear()
        self.canvas.draw_idle()
        self.progress.setRange(0,args.points if not args.identify else 1)
        self.progress.setValue(0)
        self.log.appendPlainText('Identifying DG4162…' if args.identify else
                                 'Starting DG4162 frequency sweep…' if args.enable_output else
                                 'Checking resource and previewing sweep (output unchanged)…')
        self.worker=DGWorker(args,profile)
        self.worker.progress.connect(self.on_progress)
        self.worker.completed.connect(self.on_completed)
        self.start.setEnabled(False)
        self.stop.setEnabled(True)
        self.worker.start()

    def on_progress(self,row,total):
        self.live_points.append(row)
        voltage_columns=sorted(k for k in row if k.endswith('_mean_v'))
        if voltage_columns:
            self.figure.clear()
            ax=self.figure.subplots()
            freqs=[entry['readback_hz'] for entry in self.live_points]
            for col in voltage_columns:
                values=[entry.get(col,float('nan')) for entry in self.live_points]
                ax.plot(freqs,values,marker='o',label=col[:-7].replace('_',' / '))
            if self.spacing.currentText()=='log':
                ax.set_xscale('log')
            ax.set(xlabel='DG4162 frequency (Hz)',ylabel='Pico mean voltage (V)',
                   title='Live mapped testpoints')
            ax.grid(alpha=.3)
            ax.legend(fontsize=8)
            self.canvas.draw_idle()
        self.progress.setRange(0,total)
        self.progress.setValue(row['index']+1)
        values=[f"{k[:-7]}={v:.4g} V" for k,v in row.items()
                if k.endswith('_mean_v') and isinstance(v,(int,float))]
        self.log.appendPlainText(
            f"{row['index']+1}/{total}: {row['readback_hz']:.6g} Hz, "
            f"dwell {row['dwell_s']:.3f} s" +
            (' · ' + ' · '.join(values) if values else ''))

    def on_completed(self,text,success):
        self.log.appendPlainText(('Completed: ' if success else 'Stopped/error: ')+text)
        self.start.setEnabled(True)
        self.stop.setEnabled(False)
        # Keep QThread referenced until it really finishes (Qt emits the
        # completed signal from inside run(), just before run() returns).
        # The next start replaces a finished worker safely.

    def stop_run(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel.set()
            self.log.appendPlainText('Stop requested; generator output will be disabled.')
            self.stop.setEnabled(False)

    def is_running(self):
        return bool(self.worker and self.worker.isRunning())
