"""GUI for the triggered startup capture procedure."""
from pathlib import Path
import json
import threading
import numpy as np
from PyQt6.QtCore import QThread,pyqtSignal
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QLineEdit,QComboBox,QDoubleSpinBox,QSpinBox,QCheckBox,QPushButton,QMessageBox,QFileDialog,QScrollArea)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from startup_waveform import parse_currents,parse_voltages,validate,run,draw_overlay

class Worker(QThread):
    point=pyqtSignal(object,int)
    done=pyqtSignal(str,bool)
    def __init__(self,settings,profile):
        super().__init__();self.settings=settings;self.profile=profile;self.cancel=threading.Event()
    def run(self):
        try:self.done.emit(str(run(self.settings,self.profile,self.cancel,self.point.emit)),True)
        except BaseException as e:self.done.emit(f'{type(e).__name__}: {e}',False)

class StartupPage(QWidget):
    def __init__(self,station):
        super().__init__(station);self.station=station;self.worker=None;self.records=[];self.profile=None;self.probe_corrections={};self.loaded_directory=None
        layout=QVBoxLayout(self)
        desc=QLabel('Capture the Vin trigger edge and all mapped Pico channels while switching on the selected Hameg channel. '
                    'Each current is a programmed limit, not a measured transient current. PicoScope grounds are shared; '
                    'verify probe grounds, voltage compliance, and current limits before running.')
        desc.setWordWrap(True);layout.addWidget(desc)
        form=QFormLayout();layout.addLayout(form)
        self.currents=QLineEdit('20, 30, 40, 50, 60, 80');form.addRow('Current setpoints [mA]',self.currents)
        self.voltages=QLineEdit('2.0');form.addRow('Voltage limits [V], comma-separated',self.voltages)
        self.trigger=QComboBox();form.addRow('Trigger input (rising)',self.trigger)
        refresh=QPushButton('Refresh mapped inputs');refresh.clicked.connect(self.refresh);form.addRow(refresh)
        self.boxes={}
        entries=[('trigger_v','Trigger threshold at probe tip [V]',.1,-20,20,4),('pre_ms','Pre-trigger [ms]',1,.01,1000,3),('post_ms','Post-trigger [ms]',4,.01,1000,3),('sample_rate','Sample rate [samples/s]',1000000,1000,100000000,0),('timeout_s','Trigger timeout [s]',3,.1,30,2),('off_s','Off/discharge time [s]',2,.01,300,2),('max_current_ma','Maximum permitted current setpoint [mA]',100,1,10000,3),('max_voltage_v','Verified maximum voltage setting [V]',2,.001,32,3)]
        for key,label,value,minimum,maximum,decimals in entries:
            box=QDoubleSpinBox();box.setDecimals(decimals);box.setRange(minimum,maximum);box.setValue(value);self.boxes[key]=box;form.addRow(label,box)
        self.repeats=QSpinBox();self.repeats.setRange(1,100);self.repeats.setValue(3);form.addRow('Repeats per current',self.repeats)
        self.simulate=QCheckBox('Simulate (no hardware)');form.addRow(self.simulate)
        self.verify=QCheckBox('I verified wiring, voltage/current limits, and probe grounding');form.addRow(self.verify)
        buttons=QHBoxLayout();layout.addLayout(buttons)
        self.start_btn=QPushButton('Start startup waveform sweep');self.start_btn.clicked.connect(self.start);buttons.addWidget(self.start_btn)
        stop=QPushButton('Stop');stop.clicked.connect(self.stop);buttons.addWidget(stop)
        load=QPushButton('Open saved waveform run…');load.clicked.connect(self.open_saved);buttons.addWidget(load)
        correct=QPushButton('Correct saved probe factors…');correct.clicked.connect(self.correct_probes);layout.addWidget(correct)
        self.status=QLabel('Ready');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.figure=Figure(figsize=(9,8));self.canvas=FigureCanvasQTAgg(self.figure);self.canvas.setMinimumHeight(500)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(self.canvas);layout.addWidget(scroll,1)
        self.refresh()
    def refresh(self):
        previous=self.trigger.currentData();self.trigger.clear()
        for m in self.station.snapshot().get('mappings',[]):self.trigger.addItem(f"{m['board']} / {m['testpoint']} ({m['channel']})",m['channel'])
        i=self.trigger.findData(previous)
        if i>=0:self.trigger.setCurrentIndex(i)
        else:
            for j in range(self.trigger.count()):
                if 'vin' in self.trigger.itemText(j).lower():self.trigger.setCurrentIndex(j);break
    def is_running(self):return self.worker is not None and self.worker.isRunning()
    def start(self):
        s=self.station
        if self.is_running() or s.running or s.dg4162_page.is_running():QMessageBox.warning(self,'Measurement active','Stop the active measurement first.');return
        try:
            if not self.simulate.isChecked() and not self.verify.isChecked():raise ValueError('Verify wiring and limits before starting.')
            settings={k:v.value() for k,v in self.boxes.items()}
            settings.update(currents_ma=parse_currents(self.currents.text()),voltage_limits_v=parse_voltages(self.voltages.text()),trigger_channel=self.trigger.currentData(),repeats=self.repeats.value(),simulate=self.simulate.isChecked(),port=s.port.text(),channel=s.hmp.value(),output=Path('results'),name=s.name.text())
            self.profile=validate(settings,s.snapshot());self.records=[];self.probe_corrections={};self.loaded_directory=None
            self.worker=Worker(settings,self.profile);self.worker.point.connect(self.update_plot);self.worker.done.connect(self.finished)
            self.start_btn.setEnabled(False);s.boardtabs.setEnabled(False);s.backend.setEnabled(False);self.status.setText('Starting…');self.worker.start()
        except Exception as e:QMessageBox.warning(self,'Cannot start',str(e))
    def stop(self):
        if self.is_running():self.worker.cancel.set();self.status.setText('Stopping; disabling supply…')
    def update_plot(self,record,total):
        self.records.append(record);self.status.setText(f"Capture {len(self.records)}/{total}: {record['current_ma']:g} mA / {record['voltage_limit_v']:g} V, repeat {record['repeat']}")
        self.redraw_waveforms();self.canvas.draw_idle()
    def finished(self,message,success):
        self.start_btn.setEnabled(True);self.station.boardtabs.setEnabled(True);self.station.backend.setEnabled(True)
        if success:self.loaded_directory=Path(message)
        self.status.setText(('Completed · ' if success else 'Stopped / error · ')+message)
    def redraw_waveforms(self):
        from probe_correction import waveform_view
        draw_overlay(waveform_view(self.records,self.profile,self.probe_corrections),self.profile,figure=self.figure)

    def probe_mapping_changed(self,key,value):
        if self.is_running() or not self.profile or not self.records:return
        if not any((m['board'],m['testpoint'],m['channel'])==key for m in self.profile['mappings']):return
        selected=dict(self.probe_corrections);selected[key[2]]=value
        self.apply_probe_corrections(selected)

    def apply_probe_corrections(self,selected):
        from probe_correction import waveform_view
        if self.loaded_directory:
            (self.loaded_directory/'probe_corrections.json').write_text(json.dumps(selected,indent=2)+'\n')
            draw_overlay(waveform_view(self.records,self.profile,selected),self.profile,self.loaded_directory)
        self.probe_corrections=selected
        self.redraw_waveforms();self.canvas.draw_idle()

    def correct_probes(self):
        if self.is_running() or not self.records or not self.loaded_directory:return
        from probe_correction import edit_corrections,waveform_view
        selected=edit_corrections(self,self.profile,self.probe_corrections)
        if selected is None:return
        try:
            self.apply_probe_corrections(selected)
        except Exception as e:QMessageBox.warning(self,'Could not save correction',str(e))

    def open_saved(self):
        if self.is_running():return
        folder=QFileDialog.getExistingDirectory(self,'Choose startup waveform results directory','results')
        if not folder:return
        try:
            path=Path(folder);self.loaded_directory=path
            from probe_correction import read_corrections
            self.probe_corrections=read_corrections(path)
            meta=json.loads((path/'metadata.json').read_text());self.profile=meta['profile'];self.records=[]
            for entry in meta['captures']:
                with np.load(path/entry['file'],allow_pickle=False) as f:
                    self.records.append({'current_ma':entry['current_ma'],'voltage_limit_v':entry.get('voltage_limit_v',meta.get('settings',{}).get('voltage_limit')),'repeat':entry['repeat'],'time_s':f['time_s'].copy(),'values':{m['channel']:f[m['channel']].copy() for m in self.profile['mappings']}})
            if not self.records:raise ValueError('No captured waveforms in this directory')
            self.redraw_waveforms();self.canvas.draw_idle();self.status.setText(f'Loaded {len(self.records)} captures from {path}')
        except Exception as e:QMessageBox.warning(self,'Cannot open waveform run',str(e))
