#!/usr/bin/env python3
"""Desktop RD53 test station: wiring editor and live current scans."""
import copy
import json
from pathlib import Path
import sys
import os
import threading

# Avoid system GTK/GNOME theme plugins whose GSettings schema may mismatch
# the libraries loaded by a scientific Python environment. Process-local only.
if sys.platform.startswith("linux") and os.environ.get("RD53_NATIVE_QT_THEME") != "1":
    os.environ["QT_QPA_PLATFORMTHEME"] = "generic"
    os.environ["QT_STYLE_OVERRIDE"] = "Fusion"
from PyQt6.QtCore import Qt,QThread,pyqtSignal
from PyQt6.QtGui import QColor,QPainter,QPen,QPixmap,QFont
from PyQt6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,
 QFormLayout,QLabel,QLineEdit,QPushButton,QComboBox,QCheckBox,QSpinBox,QDoubleSpinBox,
 QGroupBox,QTableWidget,QTableWidgetItem,QHeaderView,QSplitter,QTabWidget,QPlainTextEdit,
 QFileDialog,QInputDialog,QMessageBox,QProgressBar)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from mapping import default_profile,validate_profile
from scan import parser
from station import run_mapped


class Worker(QThread):
    point=pyqtSignal(object)
    done=pyqtSignal(str,bool)
    def __init__(self,args,profile):
        super().__init__();self.args=args;self.profile=profile;self.stop=threading.Event()
    def run(self):
        try:
            out=run_mapped(self.args,self.profile,self.stop,self.point.emit)
            self.done.emit(f'Completed · {out}',True)
        except BaseException as e:self.done.emit(f'{type(e).__name__}: {e}',False)


class BoardView(QWidget):
    clicked=pyqtSignal(str)
    place=pyqtSignal(float,float)
    def __init__(self):
        super().__init__();self.board={};self.mappings=[];self.selected='';self.edit=False
        self.setMinimumHeight(270)
    def rect(self):
        from PyQt6.QtCore import QRectF
        return QRectF(30,40,max(20,self.width()-60),max(20,self.height()-75))
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r=self.rect();pix=QPixmap(self.board.get('image',''))
        if not pix.isNull():p.drawPixmap(r.toRect(),pix)
        else:
            p.setBrush(QColor('#123c3b'));p.setPen(QPen(QColor('#297570'),2));p.drawRoundedRect(r,15,15)
            p.setPen(QColor('#b7dcd6'));p.setFont(QFont('Sans',11));p.drawText(r.toRect(),Qt.AlignmentFlag.AlignCenter,
                'RD53 v2 · '+self.board.get('id','')+'\nSchematic view — testpoint positions unverified')
        for tp in self.board.get('testpoints',[]):
            x=r.left()+tp['x']*r.width();y=r.top()+tp['y']*r.height()
            matches=[m for m in self.mappings if m['board']==self.board.get('id') and m['testpoint']==tp['name']]
            p.setBrush(QColor('#fbbf24' if tp['name']==self.selected else '#60d5b5' if matches else '#9cabbc'))
            p.setPen(QPen(QColor('#081820'),2));p.drawEllipse(int(x-8),int(y-8),16,16)
            p.setPen(QColor('#ffffff'));p.setFont(QFont('Sans',10))
            text=tp['name']+(' ← '+matches[0]['channel'] if matches else '')
            p.drawText(int(x-50),int(y+27),text)
        p.end()
    def mousePressEvent(self,event):
        r=self.rect();pos=event.position();x=(pos.x()-r.left())/r.width();y=(pos.y()-r.top())/r.height()
        if not 0<=x<=1 or not 0<=y<=1:return
        if self.edit:self.place.emit(x,y);return
        for tp in self.board.get('testpoints',[]):
            if ((x-tp['x'])*r.width())**2+((y-tp['y'])*r.height())**2<20**2:
                self.clicked.emit(tp['name']);return


class Window(QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle('RD53 · Powering Test Station');self.resize(1380,920)
        self.profile=default_profile();self.worker=None;self.rows=[];self.running=False
        root=QWidget();self.setCentralWidget(root);layout=QVBoxLayout(root)
        header=QHBoxLayout();title=QLabel('RD53  /  Powering Test Station');title.setStyleSheet('font-size:24px;font-weight:700;color:#e2f0ff')
        header.addWidget(title);header.addStretch();self.state=QLabel('READY');header.addWidget(self.state);layout.addLayout(header)
        sub=QLabel('Map your wiring • capture fluctuations • compare boards');sub.setStyleSheet('color:#91a6bb');layout.addWidget(sub)
        splitter=QSplitter();layout.addWidget(splitter,1)
        left=QWidget();ll=QVBoxLayout(left);splitter.addWidget(left)
        tabs=QTabWidget();ll.addWidget(tabs)
        setup=QWidget();form=QFormLayout(setup);tabs.addTab(setup,'Scan setup')
        self.backend=QComboBox();self.backend.addItems(['pico','venelin']);form.addRow('Measurement source',self.backend)
        self.port=QLineEdit('/dev/hmp4040-1');form.addRow('Hameg serial port',self.port)
        self.hmp=QSpinBox();self.hmp.setRange(1,4);self.hmp.setValue(2);form.addRow('Hameg channel',self.hmp)
        self.fields={}
        for name,label,value,maximum,decimals in [('start','Start [mA]',5,10000,3),('stop','Stop [mA]',50,10000,3),
            ('step','Step [mA]',5,10000,3),('max_current','Maximum [mA]',450,10000,3),
            ('voltage_limit','Compliance [V]',3.5,32,3),('settle','Settling [s]',1,3600,3),
            ('capture_time','Capture window [s]',.02,60,4),('sample_rate','Sample rate [S/s]',1000000,100000000,0)]:
            box=QDoubleSpinBox();box.setDecimals(decimals);box.setRange(0,maximum);box.setValue(value)
            self.fields[name]=box;form.addRow(label,box)
        self.captures=QSpinBox();self.captures.setRange(1,10000);self.captures.setValue(10);form.addRow('Captures per point',self.captures)
        self.name=QLineEdit('board1');form.addRow('Run name',self.name)
        notespage=QWidget();noteslayout=QVBoxLayout(notespage)
        noteslayout.addWidget(QLabel('Run comments — saved with metadata and every CSV row.'))
        self.comments=QPlainTextEdit();self.comments.setPlaceholderText('Board ID, wiring changes, temperature, observations, unusual behaviour…')
        noteslayout.addWidget(self.comments);tabs.addTab(notespage,'Comments')
        self.reverse=QCheckBox('Include downward sweep');self.reverse.setChecked(True);form.addRow(self.reverse)
        self.raw=QCheckBox('Save every raw capture');form.addRow(self.raw)
        self.sim=QCheckBox('Simulation — instruments remain untouched');self.sim.setChecked(True);form.addRow(self.sim)
        self.verified=QCheckBox('Wiring, fitted ADC types and voltage/current limits checked');form.addRow(self.verified)
        adcpage=QWidget();af=QFormLayout(adcpage);tabs.addTab(adcpage,'Venelin / ADC')
        self.adcfields={}
        for n,label in [('root','pmod_adc repository'),('config','Board config.toml'),('data','Decoded fadc.dat')]:
            row=QWidget();hl=QHBoxLayout(row);hl.setContentsMargins(0,0,0,0);edit=QLineEdit();btn=QPushButton('Browse')
            self.adcfields[n]=edit;hl.addWidget(edit);hl.addWidget(btn);af.addRow(label,row)
            btn.clicked.connect(lambda _,key=n:self.browse_adc(key))
        self.inputinfo=QPushButton('Show effective ADC types / units');self.inputinfo.clicked.connect(self.show_inputs);af.addRow(self.inputinfo)
        note=QLabel('Uses built install/bin helpers. Close the PMOD GUI first.\nSerial device is configured by the pmod_adc build.\nSelect 250–32000 S/s; fitted input types come from config.toml.\nNormal initialization runs before each scan; no zero calibration.');note.setWordWrap(True);af.addRow(note)
        note=QLabel('Multiple boards share one swept supply channel.\nInput assignment records wiring; it does not switch connections.\nADC inputs are not isolated: verify chain common-mode limits.');note.setWordWrap(True);ll.addWidget(note)
        buttons=QHBoxLayout();self.startbtn=QPushButton('▶  Start scan');self.startbtn.setStyleSheet('background:#177e6a;font-weight:700')
        self.stopbtn=QPushButton('■  Stop');self.stopbtn.setEnabled(False);buttons.addWidget(self.startbtn);buttons.addWidget(self.stopbtn);ll.addLayout(buttons)
        self.progress=QProgressBar();self.progress.setRange(0,100);ll.addWidget(self.progress)
        right=QWidget();rl=QVBoxLayout(right);splitter.addWidget(right);splitter.setSizes([370,950])
        tools=QHBoxLayout();self.boardselect=QComboBox();tools.addWidget(self.boardselect)
        for title,fn in [('Add board',self.add_board),('Load photo',self.load_photo),('Save profile',self.save_profile),('Load profile',self.load_profile)]:
            btn=QPushButton(title);btn.clicked.connect(fn);tools.addWidget(btn)
        rl.addLayout(tools)
        self.view=BoardView();rl.addWidget(self.view)
        editrow=QHBoxLayout();self.editpoints=QCheckBox('Place / move testpoints on click');editrow.addWidget(self.editpoints)
        hint=QLabel('Click a testpoint to select its input assignment');editrow.addWidget(hint);rl.addLayout(editrow)
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['RD53 board','Testpoint','Measurement input','Probe × (Pico)','BNC ±range [V] (Pico)'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.table.setMaximumHeight(170);rl.addWidget(self.table)
        mapbuttons=QHBoxLayout()
        for title,fn in [('Add assignment',self.add_mapping),('Remove selected',self.remove_mapping)]:
            btn=QPushButton(title);btn.clicked.connect(fn);mapbuttons.addWidget(btn)
        mapbuttons.addStretch();rl.addLayout(mapbuttons)
        rtabs=QTabWidget();rl.addWidget(rtabs,1)
        self.figure=Figure(figsize=(8,3),facecolor='#101f2e');self.canvas=FigureCanvasQTAgg(self.figure);rtabs.addTab(self.canvas,'Live measurements')
        self.log=QPlainTextEdit();self.log.setReadOnly(True);rtabs.addTab(self.log,'Run log')
        self.readout=QLabel('No measurements yet');rl.addWidget(self.readout)
        self.backend.currentTextChanged.connect(self.change_backend);self.boardselect.currentTextChanged.connect(self.refresh_board)
        self.view.clicked.connect(self.select_point);self.view.place.connect(self.place_point)
        self.editpoints.toggled.connect(lambda v:setattr(self.view,'edit',v))
        self.startbtn.clicked.connect(self.start);self.stopbtn.clicked.connect(self.stop)
        self.table.itemChanged.connect(lambda _:self.refresh_board());self.rebuild();self.redraw()
        self.setStyleSheet(self.styleSheet()+'''QMainWindow,QWidget {background:#0c1825;color:#d8e5f2;font-size:12px;}
            QLineEdit,QDoubleSpinBox,QSpinBox,QComboBox,QPlainTextEdit,QTableWidget {background:#172a3c;border:1px solid #30475d;border-radius:5px;padding:4px;}
            QPushButton {background:#22394e;border:1px solid #36546b;border-radius:6px;padding:7px;}
            QPushButton:disabled {color:#617488;} QHeaderView::section {background:#22394e;padding:5px;}
            QTabBar::tab {background:#172a3c;padding:7px;} QTabBar::tab:selected {background:#28506c;}
            QProgressBar {border:1px solid #30475d;border-radius:5px;text-align:center;} QProgressBar::chunk {background:#33b393;}''')

    def browse_adc(self,key):
        path=QFileDialog.getExistingDirectory(self,'pmod_adc repository') if key=='root' else QFileDialog.getOpenFileName(self,'Choose '+key)[0]
        if not path:return
        self.adcfields[key].setText(path)
        if key=='root':
            self.adcfields['config'].setText(str(Path(path)/'exe/pgm/config.toml'))
            self.adcfields['data'].setText(str(Path(path)/'exe/pgm/fadc.dat'))

    def show_inputs(self):
        try:
            from venelin import input_profiles
            profiles=input_profiles(self.adcfields['config'].text())
            lines=[]
            for m in self.read_mapping():
                p=profiles[m['channel']];unit='A' if p['kind']=='I' else 'V'
                full=p['full_scale_volts']/(p['shunt_ohms'] if unit=='A' else 1)
                lines.append(f"{m['channel']} → {m['board']} {m['testpoint']}: {p['kind']}, [{unit}], full scale {full:g} {unit}")
            setup=Path(self.adcfields['config'].text()).parent/'gui_board_inputs.json'
            source=str(setup) if setup.exists() else self.adcfields['config'].text()+' + confirmed RD53 defaults'
            QMessageBox.information(self,'Effective ADC input scaling','Source: '+source+'\n\n'+'\n'.join(lines))
        except Exception as e:QMessageBox.warning(self,'Cannot read ADC input setup',str(e))

    def read_mapping(self):
        result=[]
        for r in range(self.table.rowCount()):
            texts=[self.table.item(r,c).text().strip() if self.table.item(r,c) else '' for c in range(5)]
            result.append(dict(board=texts[0],testpoint=texts[1],channel=texts[2],probe=float(texts[3]),range=float(texts[4])))
        return result
    def snapshot(self):
        p=copy.deepcopy(self.profile);p['backend']=self.backend.currentText();p['mappings']=self.read_mapping()
        p['adc']={n:e.text() for n,e in self.adcfields.items()};return validate_profile(p)
    def rebuild(self):
        selected=self.boardselect.currentText();self.boardselect.blockSignals(True);self.boardselect.clear()
        self.boardselect.addItems([b['id'] for b in self.profile['boards']]);self.boardselect.setCurrentText(selected or self.profile['boards'][0]['id']);self.boardselect.blockSignals(False)
        self.table.blockSignals(True);self.table.setRowCount(len(self.profile['mappings']))
        for r,m in enumerate(self.profile['mappings']):
            for c,n in enumerate(('board','testpoint','channel','probe','range')):self.table.setItem(r,c,QTableWidgetItem(str(m[n])))
        self.table.blockSignals(False);self.refresh_board()
    def refresh_board(self,*_):
        self.view.board=next((b for b in self.profile['boards'] if b['id']==self.boardselect.currentText()),{})
        try:self.view.mappings=self.read_mapping()
        except ValueError:self.view.mappings=[]
        self.view.update()
    def select_point(self,name):
        self.view.selected=name;self.view.update()
        for r in range(self.table.rowCount()):
            if self.table.item(r,0).text()==self.boardselect.currentText() and self.table.item(r,1).text()==name:
                self.table.selectRow(r);return
    def add_board(self):
        if self.running:return
        name,ok=QInputDialog.getText(self,'New RD53 board','Unique board ID:')
        if ok and name.strip() and name.strip() not in [b['id'] for b in self.profile['boards']]:
            self.profile['mappings']=self.read_mapping();b=copy.deepcopy(default_profile()['boards'][0]);b['id']=name.strip()
            self.profile['boards'].append(b);self.rebuild();self.boardselect.setCurrentText(b['id'])
    def load_photo(self):
        if self.running:return
        path,_=QFileDialog.getOpenFileName(self,'Board photograph','','Images (*.png *.jpg *.jpeg)')
        if path:self.view.board['image']=str(Path(path).resolve());self.view.update()
    def place_point(self,x,y):
        if self.running:return
        name,ok=QInputDialog.getText(self,'Testpoint','Testpoint name (existing name moves its marker):')
        if ok and name.strip():
            point=next((p for p in self.view.board['testpoints'] if p['name']==name.strip()),None)
            if point is None:point={'name':name.strip()};self.view.board['testpoints'].append(point)
            point.update(x=x,y=y);self.view.update()
    def add_mapping(self):
        if self.running:return
        b=self.view.board;points=[p['name'] for p in b['testpoints']]
        name,ok=QInputDialog.getItem(self,'Assign input','Testpoint:',points,0,False)
        if not ok:return
        choices=list('ABCD') if self.backend.currentText()=='pico' else [f'PMOD{p}_Ch{c}' for p in range(1,7) for c in range(8)]
        channel,ok=QInputDialog.getItem(self,'Assign input','Measurement input:',choices,0,False)
        if not ok:return
        self.profile['mappings']=self.read_mapping()+[dict(board=b['id'],testpoint=name,channel=channel,probe=1,range=2)];self.rebuild()
    def remove_mapping(self):
        if self.running:return
        row=self.table.currentRow()
        if row>=0:self.table.removeRow(row);self.refresh_board()
    def change_backend(self,name):
        if self.running:return
        self.profile['mappings']=self.read_mapping()
        for i,m in enumerate(self.profile['mappings']):
            m['channel']=('ABCD'[i%4] if name=='pico' else f'PMOD{i//8+1}_Ch{i%8}')
        self.profile['backend']=name;self.rebuild()
        if name=='venelin':self.fields['sample_rate'].setValue(1000)
        else:self.fields['sample_rate'].setValue(1000000)
        self.verified.setChecked(False)
    def save_profile(self):
        if self.running:return
        try:p=self.snapshot()
        except Exception as e:QMessageBox.warning(self,'Invalid mapping',str(e));return
        path,_=QFileDialog.getSaveFileName(self,'Save wiring profile','','JSON (*.json)')
        if path:Path(path).write_text(json.dumps(p,indent=2)+'\n')
    def load_profile(self):
        if self.running:return
        path,_=QFileDialog.getOpenFileName(self,'Load wiring profile','','JSON (*.json)')
        if not path:return
        try:
            p=validate_profile(json.loads(Path(path).read_text()));self.profile=p
            self.backend.blockSignals(True);self.backend.setCurrentText(p['backend']);self.backend.blockSignals(False)
            for n,e in self.adcfields.items():e.setText(p.get('adc',{}).get(n,''))
            self.fields['sample_rate'].setValue(1000 if p['backend']=='venelin' else 1000000)
            self.verified.setChecked(False);self.rebuild()
        except Exception as e:QMessageBox.warning(self,'Profile error',str(e))
    def start(self):
        if self.running:return
        try:
            p=self.snapshot()
            if not self.sim.isChecked() and not self.verified.isChecked():raise ValueError('Check physical wiring and safe limits before a hardware scan')
            argv=['--start',str(self.fields['start'].value()/1000),'--stop',str(self.fields['stop'].value()/1000),
                  '--step',str(self.fields['step'].value()/1000),'--max-current',str(self.fields['max_current'].value()/1000),
                  '--voltage-limit',str(self.fields['voltage_limit'].value())]
            args=parser().parse_args(argv);args.port=self.port.text();args.channel=self.hmp.value();args.name=self.name.text();args.comments=self.comments.toPlainText()
            for n in ('settle','capture_time','sample_rate'):setattr(args,n,self.fields[n].value())
            args.captures=self.captures.value();args.reverse=self.reverse.isChecked();args.simulate=self.sim.isChecked();args.save_waveforms=self.raw.isChecked()
            from scan import current_points
            count=len(current_points(args.start,args.stop,args.step))*(2 if args.reverse else 1)
            self.progress.setRange(0,count);self.progress.setValue(0);self.rows=[];self.redraw();self.running=True
            self.comments.setReadOnly(True);self.table.setEnabled(False);self.backend.setEnabled(False);self.startbtn.setEnabled(False);self.stopbtn.setEnabled(True)
            self.state.setText('SIMULATING' if args.simulate else 'MEASURING');self.log.appendPlainText(json.dumps(p,indent=2))
            self.worker=Worker(args,p);self.worker.point.connect(self.point);self.worker.done.connect(self.finished);self.worker.start()
        except Exception as e:QMessageBox.warning(self,'Cannot start',str(e))
    def stop(self):
        if self.worker:self.worker.stop.set();self.state.setText('STOPPING · waiting for safe shutdown');self.stopbtn.setEnabled(False)
    def point(self,batch):
        self.rows.extend(batch);self.progress.setValue(self.progress.value()+1)
        text=' | '.join(f"{r['board']} {r['testpoint']}: {r['mean']:.5f} {r['unit']} · σ={r['sample_std']:.5g}" for r in batch)
        self.readout.setText(text);self.log.appendPlainText(text);self.redraw()
    def finished(self,text,ok):
        self.running=False;self.comments.setReadOnly(False);self.startbtn.setEnabled(True);self.stopbtn.setEnabled(False);self.table.setEnabled(True);self.backend.setEnabled(True)
        self.state.setText('COMPLETE' if ok else 'STOPPED / ERROR');self.log.appendPlainText(text)
        self.readout.setText(text)
    def redraw(self):
        self.figure.clear()
        units=list(dict.fromkeys(r['unit'] for r in self.rows)) or ['V']
        for index,unit in enumerate(units):
            ax=self.figure.add_subplot(len(units),1,index+1);ax.set_facecolor('#101f2e')
            targets=list(dict.fromkeys((r['board'],r['testpoint'],r['direction']) for r in self.rows if r['unit']==unit))
            for board,tp,direction in targets:
                rr=[r for r in self.rows if (r['board'],r['testpoint'],r['unit'],r['direction'])==(board,tp,unit,direction)]
                ax.errorbar([r['current_measured_A']*1000 for r in rr],[r['mean'] for r in rr],
                    yerr=[r['capture_std'] or 0 for r in rr],marker='o',markersize=3,label=f'{board} {tp} {direction}')
            ax.set_xlabel('Measured supply current [mA]',color='#c6d8e9')
            ax.set_ylabel('Current [A]' if unit=='A' else 'Voltage [V]',color='#c6d8e9')
            ax.tick_params(colors='#c6d8e9');ax.grid(alpha=.15)
            if targets:ax.legend(fontsize=8)
        self.figure.tight_layout();self.canvas.draw_idle()
    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():
            self.stop();self.log.appendPlainText('Close deferred until acquisition stops and supply is disabled.');event.ignore()
        else:event.accept()


if __name__=='__main__':
    app=QApplication(sys.argv);window=Window();window.show();sys.exit(app.exec())

