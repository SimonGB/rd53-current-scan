"""DG4162 generator-only sweep page embedded in the shared RD53 station."""
from __future__ import annotations

import argparse
from pathlib import Path
import threading

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QGroupBox,QLineEdit,QComboBox,
    QDoubleSpinBox,QSpinBox,QPushButton,QLabel,QCheckBox,QPlainTextEdit,
    QProgressBar,QMessageBox,QScrollArea,QSplitter,
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
        splitter=QSplitter(Qt.Orientation.Horizontal)
        settings_scroll=QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setMinimumWidth(360)
        settings_scroll.setMaximumWidth(570)
        self.settings_panel=QWidget()
        settings_layout=QVBoxLayout(self.settings_panel)
        settings_scroll.setWidget(self.settings_panel)
        splitter.addWidget(settings_scroll)
        output_panel=QWidget()
        output_layout=QVBoxLayout(output_panel)
        splitter.addWidget(output_panel)
        splitter.setStretchFactor(0,0)
        splitter.setStretchFactor(1,1)
        layout.addWidget(splitter,1)

        card=QGroupBox('Generator & waveform')
        form=QFormLayout(card)
        self.resource=QComboBox()
        self.resource.setEditable(True)
        self.resource.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.resource.setMinimumWidth(380)
        if Path('/dev/usbtmc0').exists():
            self.resource.addItem('/dev/usbtmc0')
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
        settings_layout.addWidget(card)

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
        self.capture_window=QDoubleSpinBox()
        self.capture_window.setRange(.001,2)
        self.capture_window.setDecimals(3)
        self.capture_window.setValue(.02)
        opts.addRow('Pico capture window [s]',self.capture_window)
        self.capture_rate=QDoubleSpinBox()
        self.capture_rate.setRange(1000,10_000_000)
        self.capture_rate.setDecimals(0)
        self.capture_rate.setValue(1_000_000)
        opts.addRow('Pico sample rate [S/s]',self.capture_rate)
        self.plot_quantity=QComboBox()
        self.plot_quantity.addItems([
            'Mean voltage','Peak-to-peak voltage','Standard deviation',
            'VIN→VOUT attenuation [dB]','VOUT relative phase [deg]'])
        self.plot_quantity.currentIndexChanged.connect(self.redraw_live)
        opts.addRow('Live plot quantity',self.plot_quantity)
        note=QLabel('Hameg DC current is not changed here. Each frequency gets one '
                    'short PicoScope waveform capture. At low frequencies '
                    'the capture can miss most of a cycle.')
        note.setWordWrap(True)
        opts.addRow(note)
        settings_layout.addWidget(options)

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
        settings_layout.addLayout(buttons)
        self.progress=QProgressBar()
        settings_layout.addWidget(self.progress)
        self.figure=Figure(figsize=(8,4),layout='constrained')
        self.canvas=FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(230)
        output_layout.addWidget(self.canvas,2)
        self.log=QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
        settings_layout.addWidget(self.log,1)
        settings_layout.addStretch()
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
        args.pico_capture_time=self.capture_window.value()
        args.pico_sample_rate=self.capture_rate.value()
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

    def redraw_live(self, *unused):
        if not self.live_points:
            return
        choice=self.plot_quantity.currentIndex()
        suffix=('_mean_v','_vpp_v','_std_v','_attenuation_db','_phase_deg')[choice]
        y_label=('Mean voltage [V]','Peak-to-peak voltage [V]',
                 'Standard deviation [V]','VIN→VOUT attenuation [dB]',
                 'VOUT / VIN phase [deg]')[choice]
        names=sorted({key for row in self.live_points for key in row
                      if key.endswith(suffix)})
        self.figure.clear()
        ax=self.figure.subplots()
        frequencies=[row['readback_hz'] for row in self.live_points]
        valid=False
        for name in names:
            values=[row.get(name,float('nan')) for row in self.live_points]
            if any(isinstance(v,(int,float)) and math.isfinite(v) for v in values):
                valid=True
            ax.plot(frequencies,values,marker='o',label=name[:-len(suffix)].replace('_',' / '))
        if self.spacing.currentText()=='log':
            ax.set_xscale('log')
        ax.set(xlabel='Generator frequency [Hz]',ylabel=y_label,
               title='Live PicoScope frequency response')
        if valid:
            ax.legend(fontsize=8)
        else:
            ax.text(.5,.5,'No valid samples yet (check VIN and VOUT mappings, '
                    'capture length and amplitude)',ha='center',va='center',
                    transform=ax.transAxes,wrap=True)
        ax.grid(alpha=.3)
        self.canvas.draw_idle()

    def on_progress(self,row,total):
        self.live_points.append(row)
        self.redraw_live()
        self.progress.setRange(0,total)
        self.progress.setValue(row['index']+1)
        values=[f"{k[:-7]}={v:.4g} V" for k,v in row.items()
                if k.endswith('_mean_v') and isinstance(v,(int,float))]
        quality=[f"{key[:-16]}: {value}" for key,value in row.items()
                 if key.endswith('_transfer_status') and value != 'ok']
        gain=[f"{key[:-15]} attenuation={value:.2f} dB" for key,value in row.items()
              if key.endswith('_attenuation_db') and isinstance(value,(int,float))]
        self.log.appendPlainText(
            f"{row['index']+1}/{total}: {row['readback_hz']:.6g} Hz, "
            f"dwell {row['dwell_s']:.3f} s" +
            (' · ' + ' · '.join(values) if values else '') +
            (' · ' + ' · '.join(gain) if gain else '') +
            (' · ' + ' · '.join(quality) if quality else ''))

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
