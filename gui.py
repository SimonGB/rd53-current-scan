#!/usr/bin/env python3
"""Desktop RD53 test station: wiring editor and live current scans."""
import copy
import json
from pathlib import Path
import sys
import os
import threading
import time

# Avoid system GTK/GNOME theme plugins whose GSettings schema may mismatch
# the libraries loaded by a scientific Python environment. Process-local only.
if sys.platform.startswith("linux") and os.environ.get("RD53_NATIVE_QT_THEME") != "1":
    os.environ["QT_QPA_PLATFORMTHEME"] = "generic"
    os.environ["QT_STYLE_OVERRIDE"] = "Fusion"
from PyQt6.QtCore import Qt,QThread,pyqtSignal,QRectF,QSize,QStandardPaths,QTimer
from PyQt6.QtGui import QColor,QPainter,QPen,QPixmap,QFont,QIcon,QBrush
from PyQt6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,
 QFormLayout,QLabel,QLineEdit,QPushButton,QComboBox,QCheckBox,QSpinBox,QDoubleSpinBox,
 QGroupBox,QTableWidget,QTableWidgetItem,QHeaderView,QSplitter,QTabWidget,QPlainTextEdit,
 QFileDialog,QInputDialog,QMessageBox,QProgressBar,QScrollArea,QSizePolicy,
 QDialog,QDialogButtonBox,QToolButton,QMenu,QListWidget,QListWidgetItem,QAbstractItemView,QStyledItemDelegate,QColorDialog)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from mapping import default_profile,validate_profile
from scan import parser
from station import run_mapped
from run_comments import RunComments
from board_overview import BoardOverview
from sweep_plots import draw_sweep, export_sweeps
from history_viewer import HistoryViewer
from progress_estimate import ProgressEstimate,duration
from board_setup import add_boards,auto_assign,delete_testpoint,active_profile
from connections import endpoints,endpoint_label,add_connection,connection_points,prune_connections


class AbortReasonDialog(QDialog):
    """Cannot dismiss a user abort without recording a non-empty reason."""
    def reject(self):pass

    def closeEvent(self,event):event.ignore()


class Worker(QThread):
    point=pyqtSignal(object)
    done=pyqtSignal(str,bool)
    def __init__(self,args,profile):
        super().__init__();self.args=args;self.profile=profile;self.stop=threading.Event();self.notes=RunComments(args.comments)
    def run(self):
        try:
            out=run_mapped(self.args,self.profile,self.stop,self.point.emit,notes=self.notes)
            self.done.emit(f'Completed · {out}',True)
        except BaseException as e:self.done.emit(f'{type(e).__name__}: {e}',False)


class ChannelDelegate(QStyledItemDelegate):
    def __init__(self,window):super().__init__(window.table);self.window=window
    def createEditor(self,parent,option,index):
        editor=QComboBox(parent)
        for channel,label in self.window.input_choices():editor.addItem(label,channel)
        return editor
    def setEditorData(self,editor,index):editor.setCurrentIndex(editor.findData(index.data()))
    def setModelData(self,editor,model,index):model.setData(index,editor.currentData(),Qt.ItemDataRole.EditRole)


class BoardView(QWidget):
    clicked=pyqtSignal(str)
    place=pyqtSignal(float,float)
    def __init__(self):
        super().__init__();self.board={};self.mappings=[];self.selected='';self.edit=False
        self._path=None;self._pix=QPixmap();self.compact=False
        self.setMinimumHeight(180);self.setMaximumHeight(340)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)
    def image(self):
        path=self.board.get('image','')
        if path!=self._path:self._pix=QPixmap(path);self._path=path
        return self._pix
    def content_rect(self):
        available=QRectF(24,18,max(1,self.width()-48),max(1,self.height()-48))
        pix=self.image();ratio=pix.width()/pix.height() if not pix.isNull() else 2.3
        width=min(available.width(),available.height()*ratio);height=width/ratio
        return QRectF(available.center().x()-width/2,available.center().y()-height/2,width,height)
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r=self.content_rect();pix=self.image()
        if not pix.isNull():p.drawPixmap(r.toRect(),pix)
        else:
            p.setBrush(QColor('#123c3b'));p.setPen(QPen(QColor('#297570'),2));p.drawRoundedRect(r,12,12)
            p.setPen(QColor('#b7dcd6'));p.setFont(QFont('Sans',10));p.drawText(r.toRect(),Qt.AlignmentFlag.AlignCenter,
                self.board.get('id','') if self.compact else 'RD53 v2 · '+self.board.get('id','')+'\nSchematic view — positions unverified')
        for tp in self.board.get('testpoints',[]):
            x=r.left()+tp['x']*r.width();y=r.top()+tp['y']*r.height()
            matches=[m for m in self.mappings if m['board']==self.board.get('id') and m['testpoint']==tp['name']]
            p.setBrush(QColor('#fbbf24' if tp['name']==self.selected else '#60d5b5' if matches else '#9cabbc'))
            p.setPen(QPen(QColor('#081820'),2));p.drawEllipse(int(x-7),int(y-7),14,14)
            p.setPen(QColor('#ffffff'));p.setFont(QFont('Sans',9))
            text=tp['name']+(' ← '+matches[0]['channel'] if matches and not self.compact else '')
            label_width=42 if self.compact else 170
            p.drawText(int(max(2,min(x-(20 if self.compact else 45),self.width()-label_width))),int(min(y+24,self.height()-4)),text)
        p.end()
    def mousePressEvent(self,event):
        r=self.content_rect();pos=event.position();x=(pos.x()-r.left())/r.width();y=(pos.y()-r.top())/r.height()
        if not 0<=x<=1 or not 0<=y<=1:return
        if self.edit:self.place.emit(x,y);return
        for tp in self.board.get('testpoints',[]):
            if ((x-tp['x'])*r.width())**2+((y-tp['y'])*r.height())**2<20**2:
                self.clicked.emit(tp['name']);return


class Window(QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle('RD53 · Powering Test Station')
        screen=QApplication.primaryScreen().availableGeometry();self.resize(min(1380,int(screen.width()*.95)),min(920,int(screen.height()*.92)))
        self.profile=default_profile();self.worker=None;self.rows=[];self.running=False;self.clock=None;self.estimate_state='preview'
        root=QWidget();self.setCentralWidget(root);layout=QVBoxLayout(root)
        header=QHBoxLayout();title=QLabel('RD53  /  Powering Test Station');title.setStyleSheet('font-size:24px;font-weight:700;color:#e2f0ff')
        header.addWidget(title);header.addStretch();
        self.history_windows=[]
        open_run=QPushButton('Open old measurements…');open_run.clicked.connect(self.open_history);header.addWidget(open_run)
        self.state=QLabel('READY');header.addWidget(self.state);layout.addLayout(header)
        sub=QLabel('Map your wiring • capture fluctuations • compare boards');sub.setStyleSheet('color:#91a6bb');layout.addWidget(sub)
        splitter=QSplitter();layout.addWidget(splitter,1)
        left=QWidget();ll=QVBoxLayout(left)
        side=QWidget();side.setMinimumWidth(335);side.setMaximumWidth(450);sideLayout=QVBoxLayout(side);sideLayout.setContentsMargins(0,0,0,0)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(left);sideLayout.addWidget(scroll);splitter.addWidget(side)
        tabs=QTabWidget();ll.addWidget(tabs)
        setup=QWidget();form=QFormLayout(setup);self.scan_form=form;tabs.addTab(setup,'Scan setup')
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
        self.sweep_mode=QComboBox();self.sweep_mode.addItems(['Current','Voltage'])
        self.sweep_mode.setToolTip('Voltage mode sweeps the Hameg voltage setpoint with the configured maximum current enforced as a protective current limit.')
        form.addRow('Sweep mode',self.sweep_mode)
        # Same controls keep their positions; cache each mode's distinct values.
        self._last_sweep_mode='Current'
        self._sweep_presets={'Current':{'start':5.0,'stop':50.0,'step':5.0},
                             'Voltage':{'start':0.1,'stop':1.5,'step':0.05}}
        self.sweep_mode.currentTextChanged.connect(self.update_sweep_mode)
        self.name=QLineEdit('board1');form.addRow('Run name',self.name)
        self.plot_title=QLineEdit('5 RD53b in 3 serial domains');form.addRow('Plot title',self.plot_title)
        self.plot_configuration=QLineEdit('3 serial domains: Boards (1 & 2) → (3 & 4) → 5');form.addRow('Configuration',self.plot_configuration)
        self.show_configuration=QCheckBox('Show configuration box');self.show_configuration.setChecked(True);form.addRow(self.show_configuration)
        self.show_configuration.toggled.connect(self.plot_configuration.setEnabled)
        self.show_configuration.toggled.connect(self.redraw)
        self.plot_configuration.setToolTip('Edit the note shown in the plots. The checkbox controls visibility; text is retained.')
        self.plot_configuration.textChanged.connect(self.redraw)
        self.show_difference=QCheckBox('Show Vin − Vout subplot');self.show_difference.setChecked(True);form.addRow(self.show_difference)
        self.show_difference.setToolTip('Regulator voltage drop, not shunted current. Requires Vin and Vout at the same point.')
        self.show_difference.toggled.connect(self.redraw)
        self._plot_max_presets={'Current':100.0,'Voltage':2.0}
        self.plot_max=QDoubleSpinBox();self.plot_max.setRange(.001,10000);self.plot_max.setDecimals(3);self.plot_max.setValue(100)
        form.addRow('Plot maximum [mA]',self.plot_max)
        self.update_sweep_mode()
        self.plot_title.textChanged.connect(self.redraw);self.plot_max.valueChanged.connect(self.redraw)
        self.save_plots_button=QPushButton('Save Plots…')
        self.save_plots_button.setToolTip('Export currently displayed plots to PNG and PDF after editing the title or configuration.')
        self.save_plots_button.clicked.connect(self.save_plots)
        self.save_plots_button.setEnabled(False)
        form.addRow(self.save_plots_button)
        notespage=QWidget();noteslayout=QVBoxLayout(notespage)
        noteslabel=QLabel('Edit comments during the scan — saved immediately with timestamped revisions.');noteslabel.setWordWrap(True);noteslayout.addWidget(noteslabel)
        self.comments=QPlainTextEdit();self.comments.setPlaceholderText('Board ID, wiring changes, temperature, observations, unusual behaviour…')
        self.comments.textChanged.connect(self.notes_changed)
        noteslayout.addWidget(self.comments);tabs.addTab(notespage,'Comments')
        self.reverse=QCheckBox('Include downward sweep');self.reverse.setChecked(True);form.addRow(self.reverse)
        self.raw=QCheckBox('Save every raw capture');form.addRow(self.raw)
        self.sim=QCheckBox('Simulation (no hardware)');self.sim.setChecked(True);form.addRow(self.sim)
        self.verified=QCheckBox('Wiring and safe limits verified');form.addRow(self.verified)
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
        self.stopbtn=QPushButton('■  Stop');self.stopbtn.setEnabled(False);buttons.addWidget(self.startbtn);buttons.addWidget(self.stopbtn);sideLayout.addLayout(buttons)
        self.progress=QProgressBar();self.progress.setRange(0,100)
        progressrow=QHBoxLayout();progressrow.addWidget(self.progress,1);self.eta=QLabel('Estimating…');self.eta.setMinimumWidth(115);progressrow.addWidget(self.eta);sideLayout.addLayout(progressrow)
        right=QWidget();right.setMinimumWidth(0);rl=QVBoxLayout(right);splitter.addWidget(right);splitter.setSizes([350,950])
        tools=QHBoxLayout();self.boardselect=QComboBox();self.boardselect.setMinimumContentsLength(8)
        self.boardselect.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);tools.addWidget(self.boardselect)
        self.include=QCheckBox('Include in scan');self.include.setChecked(True);self.include.toggled.connect(self.enable_board);tools.addWidget(self.include)
        quick=QPushButton('Quick setup…');quick.clicked.connect(self.quick_setup);tools.addWidget(quick)
        tool=QToolButton();tool.setText('Board tools');tool.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup);menu=QMenu(tool)
        for title,fn in [('Add boards…',self.add_board),('Duplicate selected',self.duplicate_board),('Auto-assign inputs…',self.auto_inputs),
                         ('Remove selected board',self.remove_board),('Load photo',self.load_photo),('Import thesis PDF figure…',self.import_thesis),
                         ('Save profile',self.save_profile),('Load profile',self.load_profile)]:
            menu.addAction(title,fn)
        tool.setMenu(menu);tools.addWidget(tool);tools.addStretch();rl.addLayout(tools)
        self.boardtabs=QTabWidget();self.overview=BoardOverview(BoardView);self.boardtabs.addTab(self.overview,'All boards · assign inputs')
        self.view=BoardView();self.boardtabs.addTab(self.view,'Selected board · place markers');rl.addWidget(self.boardtabs)
        self.overview.assigned.connect(self.assign_from_card);self.overview.enabled.connect(self.include_from_card)
        self.overview.focused.connect(self.focus_from_card);self.overview.placed.connect(self.place_from_card)
        editrow=QHBoxLayout();self.editpoints=QCheckBox('Place / move testpoints on click');editrow.addWidget(self.editpoints)
        remove=QPushButton('Remove testpoint');remove.clicked.connect(self.remove_testpoint);editrow.addWidget(remove);editrow.addStretch();rl.addLayout(editrow)
        self.table=QTableWidget(0,6);self.table.setHorizontalHeaderLabels(['RD53 board','Testpoint','Measurement input','Probe × (Pico)','BNC ±range [V] (Pico)','Plot color'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.table.setMaximumHeight(170);self.table.setMinimumHeight(110);rl.addWidget(self.table)
        self.table.setItemDelegateForColumn(2,ChannelDelegate(self))
        self.table.cellDoubleClicked.connect(self.choose_plot_color)
        mapbuttons=QHBoxLayout()
        for title,fn in [('Add assignment',self.add_mapping),('Add connection…',self.add_connection_dialog),('Remove selected',self.remove_mapping)]:
            btn=QPushButton(title);btn.clicked.connect(fn);mapbuttons.addWidget(btn)
        self.showall=QCheckBox('All wiring rows');self.showall.toggled.connect(self.refresh_board);mapbuttons.addWidget(self.showall)
        self.plotall=QCheckBox('Plot all boards');self.plotall.setChecked(True);self.plotall.toggled.connect(self.redraw);mapbuttons.addWidget(self.plotall)
        mapbuttons.addStretch();rl.addLayout(mapbuttons)
        rtabs=QTabWidget();rl.addWidget(rtabs,1)
        self.figure=Figure(figsize=(7,5),facecolor='white');self.canvas=FigureCanvasQTAgg(self.figure)
        self.down_figure=Figure(figsize=(7,5),facecolor='white');self.down_canvas=FigureCanvasQTAgg(self.down_figure)
        for canvas,label in [(self.canvas,'Up sweep'),(self.down_canvas,'Down sweep')]:
            canvas.setMinimumSize(200,280);canvas.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)
            plot_scroll=QScrollArea();plot_scroll.setWidgetResizable(True);plot_scroll.setWidget(canvas);rtabs.addTab(plot_scroll,label)
        self.log=QPlainTextEdit();self.log.setReadOnly(True);rtabs.addTab(self.log,'Run log')
        self.readout=QLabel('No measurements yet');self.readout.setWordWrap(True);self.readout.setMaximumHeight(65);self.readout.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);rl.addWidget(self.readout)
        self.backend.currentTextChanged.connect(self.change_backend);self.boardselect.currentTextChanged.connect(self.refresh_board)
        self.view.clicked.connect(self.select_point);self.view.place.connect(self.place_point)
        self.editpoints.toggled.connect(self.set_edit_mode)
        self.startbtn.clicked.connect(self.start);self.stopbtn.clicked.connect(self.stop)
        self.table.itemChanged.connect(self.mapping_changed);self.rebuild();self.redraw()
        self.eta_timer=QTimer(self);self.eta_timer.setInterval(1000);self.eta_timer.timeout.connect(self.update_eta);self.eta_timer.start()
        for box in self.fields.values():box.valueChanged.connect(self.reset_eta)
        self.captures.valueChanged.connect(self.reset_eta);self.reverse.toggled.connect(self.reset_eta);self.sim.toggled.connect(self.reset_eta)
        self.update_eta()
        self.setStyleSheet(self.styleSheet()+'''QMainWindow,QWidget {background:#0c1825;color:#d8e5f2;font-size:12px;}
            QLineEdit,QDoubleSpinBox,QSpinBox,QComboBox,QPlainTextEdit,QTableWidget {background:#172a3c;border:1px solid #30475d;border-radius:5px;padding:4px;}
            QPushButton {background:#22394e;border:1px solid #36546b;border-radius:6px;padding:7px;}
            QPushButton:disabled {color:#617488;} QHeaderView::section {background:#22394e;padding:5px;}
            QTabBar::tab {background:#172a3c;padding:7px;} QTabBar::tab:selected {background:#28506c;}
            QProgressBar {border:1px solid #30475d;border-radius:5px;text-align:center;} QProgressBar::chunk {background:#33b393;}''')

    def sync_overview(self):
        try:mappings=self.read_mapping()
        except ValueError:mappings=[]
        self.overview.sync(self.profile['boards'],mappings,self.input_choices(),self.running,self.editpoints.isChecked(),self.boardselect.currentText(),self.view.selected)

    def assign_from_card(self,board,point,channel):
        if self.running:return
        if channel and (board,point) in connection_points(self.profile):
            if self.backend.currentText()!='venelin' or self.fitted_inputs()[channel]['kind']!='dV':
                QMessageBox.warning(self,'Differential input required','Use an unused dV input for a connection measurement.');self.sync_overview();return
        mappings=self.read_mapping()
        if channel and any(m['channel']==channel and (m['board'],m['testpoint'])!=(board,point) for m in mappings):
            QMessageBox.warning(self,'Input already assigned','Choose an unused input, or clear its existing assignment first.');self.sync_overview();return
        existing=next((m for m in mappings if (m['board'],m['testpoint'])==(board,point)),None)
        mappings=[m for m in mappings if (m['board'],m['testpoint'])!=(board,point)]
        if channel:
            record=existing or dict(board=board,testpoint=point,probe=1,range=2)
            record['channel']=channel;mappings.append(record)
        self.profile['mappings']=mappings;self.verified.setChecked(False);self.rebuild()

    def include_from_card(self,board,value):
        if self.running:return
        next(b for b in self.profile['boards'] if b['id']==board)['enabled']=value
        self.verified.setChecked(False);self.refresh_board()

    def focus_from_card(self,board,point):
        self.boardselect.setCurrentText(board);self.select_point(point)

    def place_from_card(self,board,x,y):
        if self.running:return
        self.boardselect.setCurrentText(board);self.place_point(x,y)

    def set_edit_mode(self,value):
        self.view.edit=value and not self.running;self.sync_overview()

    def new_estimate(self):
        from scan import current_points
        count=len(current_points(self.fields['start'].value(),self.fields['stop'].value(),self.fields['step'].value()))*(2 if self.reverse.isChecked() else 1)
        recording=self.captures.value()*self.fields['capture_time'].value()
        overhead=.7 if self.backend.currentText()=='venelin' else .5+self.captures.value()*.02
        if self.sim.isChecked():return ProgressEstimate(count,.05,startup=.1,finalization=2)
        return ProgressEstimate(count,self.fields['settle'].value()+recording+overhead,
            startup=5 if self.backend.currentText()=='venelin' else 2,finalization=2)

    def reset_eta(self,*_):
        if not self.running:self.estimate_state='preview';self.clock=None
        self.update_eta()

    def update_eta(self):
        try:
            if not self.running and self.estimate_state=='preview':
                estimate=self.new_estimate();self.eta.setText('≈ '+duration(estimate.remaining(time.monotonic()))+' total')
                self.eta.setToolTip('Initial estimate includes recording, settling and provisional setup/readout overhead. Refines after completed points.');return
            if self.clock is None:return
            now=time.monotonic();elapsed=duration(now-self.clock.started)
            if self.estimate_state=='done':text='Done · '+elapsed
            elif self.estimate_state=='stopped':text='Stopped · '+elapsed
            elif self.estimate_state=='stopping':text='Stopping…'
            else:
                remaining=self.clock.remaining(now)
                text='Finalizing…' if remaining is None else '≈ '+duration(remaining)+' left'
            self.eta.setText(text);self.eta.setToolTip('Elapsed: '+elapsed+'; estimate adapts to recent completed points. Initialization, timeouts and final file saving may take longer.')
        except (ValueError,OverflowError):self.eta.setText('Set scan range')

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
            texts=[self.table.item(r,c).text().strip() if self.table.item(r,c) else '' for c in range(6)]
            result.append(dict(board=texts[0],testpoint=texts[1],channel=texts[2],
                               probe=float(texts[3]),range=float(texts[4]),
                               color='' if texts[5].lower() in ('auto','default','') else texts[5]))
        return result

    def choose_plot_color(self, row, col):
        if col != 5 or self.running:
            return
        item=self.table.item(row,5)
        if item is None:
            return
        menu=QMessageBox(self)
        menu.setWindowTitle('Testpoint plot color')
        menu.setText('Choose a line color or use the automatic board color.')
        pick=menu.addButton('Choose color…', QMessageBox.ButtonRole.AcceptRole)
        reset=menu.addButton('Use board color', QMessageBox.ButtonRole.ResetRole)
        menu.addButton(QMessageBox.StandardButton.Cancel)
        menu.exec()
        if menu.clickedButton()==reset:
            item.setText('Auto')
        elif menu.clickedButton()==pick:
            current=QColor(item.text())
            color=QColorDialog.getColor(current if current.isValid() else QColor('#56B4E9'),self,'Choose plot color')
            if color.isValid():
                item.setText(color.name())

    def mapping_changed(self, item):
        if item.column()==5:
            color=item.text().strip()
            self.table.blockSignals(True)
            try:
                if color and QColor(color).isValid():
                    item.setBackground(QBrush(QColor(color)))
                else:
                    item.setBackground(QBrush())
            finally:
                self.table.blockSignals(False)
            if self.rows:
                try:
                    assigned={(m['board'],m['testpoint']):m.get('color','') for m in self.read_mapping()}
                    for record in self.rows:
                        record['color']=assigned.get((record['board'],record['testpoint']),record.get('color',''))
                except (ValueError,TypeError):
                    pass
        self.refresh_board()
    def snapshot(self):
        p=copy.deepcopy(self.profile);p['backend']=self.backend.currentText();p['mappings']=self.read_mapping()
        p['adc']={n:e.text() for n,e in self.adcfields.items()};return validate_profile(p)
    def rebuild(self):
        selected=self.boardselect.currentText();self.boardselect.blockSignals(True);self.boardselect.clear()
        self.boardselect.addItems([b['id'] for b in self.profile['boards']]);self.boardselect.setCurrentText(selected if selected in [b['id'] for b in self.profile['boards']] else self.profile['boards'][0]['id']);self.boardselect.blockSignals(False)
        self.table.blockSignals(True);self.table.setRowCount(len(self.profile['mappings']))
        for r,m in enumerate(self.profile['mappings']):
            for c,n in enumerate(('board','testpoint','channel','probe','range')):
                self.table.setItem(r,c,QTableWidgetItem(str(m[n])))
            color=m.get('color','')
            item=QTableWidgetItem(color or 'Auto')
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setToolTip('Double-click for a color picker. Auto = board color.')
            if color and QColor(color).isValid():
                item.setBackground(QBrush(QColor(color)))
            self.table.setItem(r,5,item)
        self.table.blockSignals(False);self.refresh_board()
    def refresh_board(self,*_):
        self.view.board=next((b for b in self.profile['boards'] if b['id']==self.boardselect.currentText()),{})
        try:self.view.mappings=self.read_mapping()
        except ValueError:self.view.mappings=[]
        self.include.blockSignals(True);self.include.setChecked(self.view.board.get('enabled',True));self.include.blockSignals(False)
        for row in range(self.table.rowCount()):self.table.setRowHidden(row,not self.showall.isChecked() and self.table.item(row,0).text()!=self.boardselect.currentText())
        self.view.update();self.sync_overview();self.redraw()
    def select_point(self,name):
        self.view.selected=name;self.view.update();self.sync_overview()
        for r in range(self.table.rowCount()):
            if self.table.item(r,0).text()==self.boardselect.currentText() and self.table.item(r,1).text()==name:
                self.table.selectRow(r);return
    def fitted_inputs(self):
        from venelin import input_profiles
        path=self.adcfields['config'].text().strip()
        return input_profiles(path or None)

    def input_choices(self):
        if self.backend.currentText()=='pico':return [(c,c) for c in 'ABCD']
        try:profiles=self.fitted_inputs()
        except Exception:return [(f'PMOD{p}_Ch{c}',f'PMOD{p}_Ch{c}') for p in range(1,7) for c in range(8)]
        return [(c,f"{c} · {p['kind']} [{ 'A' if p['kind']=='I' else 'V'}]") for c,p in profiles.items()]

    def modules_dialog(self):
        current=sorted({int(m['channel'][4]) for m in self.read_mapping() if m['channel'].startswith('PMOD')}) or [1,2]
        text,ok=QInputDialog.getText(self,'Available ADC modules','Installed PMOD module numbers (comma-separated):',text=','.join(map(str,current)))
        if not ok:return None
        return [int(x.strip()) for x in text.split(',') if x.strip()]

    def quick_setup(self):
        if self.running:return
        dialog=QDialog(self);dialog.setWindowTitle('Quick multi-board setup');layout=QFormLayout(dialog)
        count=QSpinBox();count.setRange(1,200);count.setValue(len(self.profile['boards']));layout.addRow('Total RD53 boards',count)
        template=QComboBox();template.addItems(['Selected board testpoints / photo','Standard Vref + Vout','Standard Vin + Vref + Vout']);layout.addRow('Use for new boards',template)
        modules=QLineEdit('1,2');layout.addRow('Installed PMOD modules',modules)
        auto=QCheckBox('Assign unused fitted inputs for me');auto.setChecked(self.backend.currentText()=='venelin');layout.addRow(auto)
        hint=QLabel('Existing boards and assignments are preserved. New boards copy a template. Review the proposed wiring before connecting signals.');hint.setWordWrap(True);layout.addRow(hint)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addRow(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:
            profile=copy.deepcopy(self.profile);profile['mappings']=self.read_mapping()
            if count.value()<len(profile['boards']):raise ValueError('Use Remove selected board to reduce the board count; existing boards are preserved here.')
            base=copy.deepcopy(self.view.board)
            if template.currentIndex():
                names=['Vref','Vout'] if template.currentIndex()==1 else ['Vin','Vref','Vout']
                base={'image':'','testpoints':[dict(name=n,x=(i+1)/(len(names)+1),y=.7,unit='V') for i,n in enumerate(names)]}
            if count.value()>len(profile['boards']):profile,_=add_boards(profile,count.value()-len(profile['boards']),base)
            if auto.isChecked():
                profile['backend']=self.backend.currentText()
                profile=auto_assign(profile,self.fitted_inputs(),[int(x.strip()) for x in modules.text().split(',') if x.strip()])
            self.profile=profile;self.verified.setChecked(False);self.rebuild()
            self.showall.setChecked(True)
            self.log.appendPlainText('Quick setup created. Review wiring; no physical connections have been switched.')
        except Exception as e:QMessageBox.warning(self,'Setup could not be applied',str(e))

    def add_board(self):
        if self.running:return
        count,ok=QInputDialog.getInt(self,'Add RD53 boards','Number to add (copies selected testpoints and photo):',1,1,200)
        if ok:
            self.profile['mappings']=self.read_mapping();self.profile,added=add_boards(self.profile,count,self.view.board)
            self.verified.setChecked(False);self.rebuild();self.boardselect.setCurrentText(added[0])

    def duplicate_board(self):
        if self.running:return
        self.profile['mappings']=self.read_mapping();self.profile,added=add_boards(self.profile,1,self.view.board)
        self.rebuild();self.boardselect.setCurrentText(added[0]);self.verified.setChecked(False)

    def auto_inputs(self):
        if self.running:return
        try:
            modules=self.modules_dialog()
            if modules is None:return
            profile=copy.deepcopy(self.profile);profile['backend']=self.backend.currentText();profile['mappings']=self.read_mapping()
            self.profile=auto_assign(profile,self.fitted_inputs(),modules);self.rebuild();self.showall.setChecked(True);self.verified.setChecked(False)
        except Exception as e:QMessageBox.warning(self,'Auto-assignment',str(e))

    def enable_board(self,value):
        if self.running:
            self.include.blockSignals(True);self.include.setChecked(self.view.board.get('enabled',True));self.include.blockSignals(False);return
        self.view.board['enabled']=value;self.verified.setChecked(False);self.sync_overview()

    def remove_board(self):
        if self.running:return
        if len(self.profile['boards'])<=1:QMessageBox.information(self,'Keep one board','At least one board is required.');return
        target=self.boardselect.currentText();self.profile['mappings']=self.read_mapping()
        self.profile['boards']=[b for b in self.profile['boards'] if b['id']!=target]
        self.profile['mappings']=[m for m in self.profile['mappings'] if m['board']!=target]
        prune_connections(self.profile)
        self.boardselect.setCurrentText(self.profile['boards'][0]['id']);self.rebuild();self.verified.setChecked(False)

    def remove_testpoint(self):
        if self.running:return
        names=[p['name'] for p in self.view.board.get('testpoints',[])]
        if not names:return
        selected=self.view.selected if self.view.selected in names else names[0]
        name,ok=QInputDialog.getItem(self,'Remove testpoint','Removes its marker and input assignment on this board:',names,names.index(selected),False)
        if ok:
            self.profile['mappings']=self.read_mapping();self.profile=delete_testpoint(self.profile,self.boardselect.currentText(),name)
            self.view.selected='';self.rebuild();self.verified.setChecked(False)

    def import_thesis(self):
        if self.running:return
        path,_=QFileDialog.getOpenFileName(self,'Select downloaded Zorn thesis','','PDF (*.pdf)')
        if not path:return
        try:
            from thesis_import import candidates
            figures=candidates(path)
            if not figures:raise ValueError('No suitable embedded images found; export the board figure as PNG and use Load photo.')
            dialog=QDialog(self);dialog.setWindowTitle('Choose regulator board figure');dialog.resize(750,540);layout=QVBoxLayout(dialog)
            hint=QLabel('Board-related pages are listed first. Select the actual PCB figure; marker positions remain editable.');hint.setWordWrap(True);layout.addWidget(hint)
            items=QListWidget();items.setIconSize(QSize(220,150));layout.addWidget(items)
            for figure in figures:
                pix=QPixmap();pix.loadFromData(figure['data'])
                item=QListWidgetItem(QIcon(pix),f"PDF page {figure['page']} · {figure['width']} × {figure['height']} px");items.addItem(item)
            items.setCurrentRow(0)
            buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
            if dialog.exec()!=QDialog.DialogCode.Accepted:return
            figure=figures[items.currentRow()]
            import hashlib
            folder=Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))/'board-images';folder.mkdir(parents=True,exist_ok=True)
            destination=folder/(hashlib.sha256(figure['data']).hexdigest()[:16]+'.'+figure['extension']);destination.write_bytes(figure['data'])
            self.view.board['image']=str(destination);self.view.board['image_source']=f"{Path(path).name}, PDF page {figure['page']}"
            self.view.update()
        except Exception as e:QMessageBox.warning(self,'PDF import failed',str(e))

    def load_photo(self):
        if self.running:return
        path,_=QFileDialog.getOpenFileName(self,'Board photograph','','Images (*.png *.jpg *.jpeg)')
        if path:self.view.board['image']=str(Path(path).resolve());self.view.update()
    def place_point(self,x,y):
        if self.view.board.get('kind')=='connections':return
        if self.running:return
        name,ok=QInputDialog.getText(self,'Testpoint','Testpoint name (existing name moves its marker):')
        if ok and name.strip():
            point=next((p for p in self.view.board['testpoints'] if p['name']==name.strip()),None)
            if point is None:point={'name':name.strip()};self.view.board['testpoints'].append(point)
            point.update(x=x,y=y);self.view.update()
    def add_connection_dialog(self):
        if self.running:return
        if self.backend.currentText()!='venelin':
            QMessageBox.information(self,'Differential input required','Select Venelin / ADC to measure between two nodes. Pico channel grounds are shared.');return
        try:
            fitted=self.fitted_inputs();used={m['channel'] for m in self.read_mapping()}
            choices=[(c,label) for c,label in self.input_choices() if c not in used and fitted[c]['kind']=='dV']
            if not choices:raise ValueError('No unused dV input. Free an input or select another installed PMOD.')
        except Exception as e:QMessageBox.warning(self,'Connection setup',str(e));return
        dialog=QDialog(self);dialog.setWindowTitle('Measure between two nodes');form=QFormLayout(dialog)
        positive=QComboBox();negative=QComboBox();channel=QComboBox()
        for endpoint in endpoints(self.profile):
            positive.addItem(endpoint_label(endpoint),endpoint);negative.addItem(endpoint_label(endpoint),endpoint)
        positive.setCurrentIndex(1 if positive.count()>1 else 0)
        for c,label in choices:channel.addItem(label,c)
        form.addRow('Positive (+) endpoint',positive);form.addRow('Negative (−) endpoint',negative);form.addRow('Physical dV input',channel)
        note=QLabel('Records V(+) − V(−). Wire the two endpoints to this differential input.\nGlobal GND is the reference node you choose in your setup.\nVerify both input pins are within the ADC common-mode and differential limits.\nSoftware does not switch wiring or isolate grounds.');note.setWordWrap(True);form.addRow(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);form.addRow(buttons)
        buttons.rejected.connect(dialog.reject)
        def save():
            try:
                profile=copy.deepcopy(self.profile);profile['mappings']=self.read_mapping()
                self.profile=add_connection(profile,positive.currentData(),negative.currentData(),channel.currentData())
                self.verified.setChecked(False);self.rebuild();dialog.accept()
            except Exception as e:QMessageBox.warning(dialog,'Connection setup',str(e))
        buttons.accepted.connect(save);dialog.exec()

    def add_mapping(self):
        if self.running:return
        if self.view.board.get('kind')=='connections':self.add_connection_dialog();return
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
        self.profile['mappings']=self.read_mapping();self.profile['backend']=name
        if name=='venelin':
            self.fields['sample_rate'].setValue(1000)
            try:self.profile=auto_assign(self.profile,self.fitted_inputs(),[1,2],replace=True)
            except Exception as e:
                self.profile['mappings']=[];self.log.appendPlainText('Use Auto-assign inputs with your installed modules: '+str(e))
        else:
            self.fields['sample_rate'].setValue(1000000)
            for i,m in enumerate(self.profile['mappings']):m['channel']='ABCD'[i%4]
        self.rebuild();self.verified.setChecked(False)
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
    def update_sweep_mode(self):
        """Keep scan settings at stable rows when switching modes."""
        mode=self.sweep_mode.currentText()
        previous=self._last_sweep_mode
        if mode != previous:
            self._plot_max_presets[previous]=self.plot_max.value()
            self._sweep_presets[previous] = {
                key:self.fields[key].value() for key in ('start','stop','step')
            }
        voltage=(mode=='Voltage')
        for key in ('start','stop','step'):
            widget=self.fields[key]
            widget.blockSignals(True)
            widget.setDecimals(4 if voltage else 3)
            widget.setRange(0.0001 if voltage else 0.001,32 if voltage else 10000)
            widget.setSingleStep(0.01 if voltage else 1)
            widget.setValue(self._sweep_presets[mode][key])
            widget.blockSignals(False)
            label=self.scan_form.labelForField(widget)
            if label is not None:
                label.setText(key.capitalize()+(' [V]' if voltage else ' [mA]'))
        maximum_label=self.scan_form.labelForField(self.fields['max_current'])
        if maximum_label is not None:
            maximum_label.setText('Current limit [mA]' if voltage else 'Maximum current [mA]')
        compliance_label=self.scan_form.labelForField(self.fields['voltage_limit'])
        if compliance_label is not None:
            compliance_label.setText('Maximum voltage [V]' if voltage else 'Compliance [V]')
        self.plot_max.blockSignals(True)
        self.plot_max.setRange(.001,32 if voltage else 10000)
        self.plot_max.setDecimals(3)
        self.plot_max.setValue(self._plot_max_presets[mode])
        self.plot_max.blockSignals(False)
        max_label=self.scan_form.labelForField(self.plot_max)
        if max_label is not None:
            max_label.setText('Plot maximum [V]' if voltage else 'Plot maximum [mA]')
        self._last_sweep_mode=mode
        if hasattr(self,'canvas'):
            self.redraw()
        if hasattr(self,'eta'):
            self.reset_eta()

    def start(self):
        if self.running or getattr(self,'abort_dialog',None):return
        try:
            p=validate_profile(active_profile(self.snapshot()))
            if not self.sim.isChecked() and not self.verified.isChecked():raise ValueError('Check physical wiring and safe limits before a hardware scan')
            voltage_mode=self.sweep_mode.currentText()=='Voltage'
            points=self.fields
            divisor=1 if voltage_mode else 1000
            argv=['--sweep-mode','voltage' if voltage_mode else 'current',
                  '--start',str(points['start'].value()/divisor),'--stop',str(points['stop'].value()/divisor),
                  '--step',str(points['step'].value()/divisor),'--max-current',str(self.fields['max_current'].value()/1000),
                  '--voltage-limit',str(self.fields['voltage_limit'].value())]
            args=parser().parse_args(argv);args.port=self.port.text();args.channel=self.hmp.value();args.name=self.name.text();args.plot_title=self.plot_title.text();args.plot_configuration=self.plot_configuration.text();args.plot_hide_configuration=not self.show_configuration.isChecked();args.plot_hide_difference=not self.show_difference.isChecked();args.plot_max_ma=self._plot_max_presets['Current'] if voltage_mode else self.plot_max.value();args.plot_max_v=self.plot_max.value() if voltage_mode else self._plot_max_presets['Voltage'];args.comments=self.comments.toPlainText()
            for n in ('settle','capture_time','sample_rate'):setattr(args,n,self.fields[n].value())
            args.captures=self.captures.value();args.reverse=self.reverse.isChecked();args.simulate=self.sim.isChecked();args.save_waveforms=self.raw.isChecked()
            from scan import current_points
            count=len(current_points(args.start,args.stop,args.step))*(2 if args.reverse else 1)
            self.progress.setRange(0,count);self.progress.setValue(0);self.rows=[];self.save_plots_button.setEnabled(False);self.redraw();self.running=True
            self.clock=self.new_estimate();self.clock.start(time.monotonic());self.estimate_state='running';self.update_eta();self.sync_overview()
            for card in self.overview.cards.values():card.status.setText('Waiting for data…')
            self.table.setEnabled(False);self.backend.setEnabled(False);self.sweep_mode.setEnabled(False);self.startbtn.setEnabled(False);self.stopbtn.setEnabled(True)
            self.state.setText('SIMULATING' if args.simulate else 'MEASURING');self.log.appendPlainText(json.dumps(p,indent=2))
            self.worker=Worker(args,p);self.worker.point.connect(self.point);self.worker.done.connect(self.finished);self.worker.start()
        except Exception as e:QMessageBox.warning(self,'Cannot start',str(e))
    def notes_changed(self):
        if self.running and self.worker:
            try:self.worker.notes.update(self.comments.toPlainText())
            except Exception as e:
                self.log.appendPlainText(f'Could not save comments: {e}')
                self.stop(reason=f'Automatic stop: could not save comments: {e}')

    def stop(self,checked=False,reason=None):
        if not self.worker or not self.running:return
        if self.worker.stop.is_set():return
        # Never delay shutdown while a user is writing a reason.
        self.worker.stop.set();self.state.setText('STOPPING · waiting for safe shutdown');self.stopbtn.setEnabled(False);self.estimate_state='stopping';self.update_eta()
        try:
            self.worker.notes.request_abort('automatic' if reason else 'user')
            if reason:self.worker.notes.abort_reason(reason)
        except Exception as e:self.log.appendPlainText(f'Could not persist abort information: {e}')
        if reason:return
        dialog=AbortReasonDialog(self);self.abort_dialog=dialog;dialog.setWindowTitle('Reason for aborting this scan')
        form=QVBoxLayout(dialog);form.addWidget(QLabel('Shutdown has been requested. Enter a reason to keep with the run data.'))
        edit=QPlainTextEdit();edit.setPlaceholderText('For example: board 3 output unstable, checking wiring');form.addWidget(edit)
        save=QPushButton('Save abort reason');save.setEnabled(False);form.addWidget(save)
        edit.textChanged.connect(lambda:save.setEnabled(bool(edit.toPlainText().strip())))
        notes=self.worker.notes
        def store_reason():
            try:notes.abort_reason(edit.toPlainText())
            except Exception as e:QMessageBox.warning(dialog,'Could not save reason',str(e));return
            self.log.appendPlainText('Abort reason: '+edit.toPlainText().strip());self.abort_dialog=None
            self.startbtn.setEnabled(not self.running);dialog.accept()
        save.clicked.connect(store_reason);dialog.setModal(True);dialog.show()
    def point(self,batch):
        self.rows.extend(batch);self.progress.setValue(self.progress.value()+1)
        if self.clock:self.clock.point(time.monotonic())
        self.overview.readings(batch);self.update_eta()
        text=' | '.join(f"{r['board']} {r['testpoint']}: {r['mean']:.5f} {r['unit']} · σ={r['sample_std']:.5g}" for r in batch)
        selected=[r for r in batch if r['board']==self.boardselect.currentText()]
        summary=' | '.join(f"{r['testpoint']}: {r['mean']:.5f} {r['unit']}" for r in selected)
        self.readout.setText(f"{len(batch)} inputs · "+summary);self.readout.setToolTip(text);self.log.appendPlainText(text);self.save_plots_button.setEnabled(bool(self.rows));self.redraw()
    def finished(self,text,ok):
        self.running=False;self.comments.setReadOnly(False);self.startbtn.setEnabled(not bool(getattr(self,'abort_dialog',None)));self.stopbtn.setEnabled(False);self.table.setEnabled(True);self.backend.setEnabled(True);self.sweep_mode.setEnabled(True)
        self.state.setText('COMPLETE' if ok else 'STOPPED / ERROR');self.log.appendPlainText(text)
        self.estimate_state='done' if ok else 'stopped';self.update_eta();self.sync_overview()
        self.readout.setText(text);self.readout.setToolTip(text)
    def plot_axis_mode(self, rows=None):
        """Use the acquisition's recorded mode, not a later mode-selector edit."""
        rows=self.rows if rows is None else rows
        if rows:
            return 'voltage' if rows[0].get('sweep_mode')=='voltage' else 'current'
        return self.sweep_mode.currentText().lower()

    def plot_limit(self, rows=None):
        mode='Voltage' if self.plot_axis_mode(rows)=='voltage' else 'Current'
        if mode==self.sweep_mode.currentText():
            return self.plot_max.value()
        return self._plot_max_presets[mode]

    def save_plots(self):
        """Save visible up/down sweeps as PNG/PDF without a new measurement."""
        rows = self.rows if self.plotall.isChecked() else [
            r for r in self.rows if r['board'] == self.boardselect.currentText()
        ]
        if not rows:
            QMessageBox.information(self, 'No measurements', 'No measurements to save yet.')
            return
        folder = QFileDialog.getExistingDirectory(self, 'Save plots to folder', 'results')
        if not folder:
            return
        output = Path(folder)
        existing = [f'mapped_voltages_{direction}.{ext}'
                    for direction in ('up', 'down')
                    if any(r['direction'] == direction for r in rows)
                    for ext in ('png', 'pdf')
                    if (output / f'mapped_voltages_{direction}.{ext}').exists()]
        if existing:
            response = QMessageBox.question(
                self, 'Replace existing plots?',
                'These files already exist and would be replaced:\n' +
                '\n'.join(existing) + '\n\nContinue?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if response != QMessageBox.StandardButton.Yes:
                return
        try:
            export_sweeps(
                rows, output, self.plot_title.text(), self.plot_limit(rows),
                self.plot_configuration.text() if self.show_configuration.isChecked() else '',
                show_difference=self.show_difference.isChecked(), color_rows=self.rows)
            QMessageBox.information(self, 'Plots saved', f'PNG and PDF plots saved to:\n{output}')
        except Exception as exc:
            QMessageBox.warning(self, 'Cannot save plots', str(exc))

    def open_history(self):
        path,_=QFileDialog.getOpenFileName(self,'Open saved run','results','Run data (metadata.json measurements.csv);;JSON/CSV (*.json *.csv)')
        if not path:return
        try:
            viewer=HistoryViewer(path,self)
            self.history_windows.append(viewer)
            viewer.finished.connect(lambda _code,v=viewer:self.history_windows.remove(v) if v in self.history_windows else None)
            viewer.show()
        except Exception as exc:
            QMessageBox.warning(self,'Cannot open saved run',str(exc))

    def redraw(self):
        if not hasattr(self,'canvas'):
            return
        visible=self.rows if self.plotall.isChecked() else [r for r in self.rows if r['board']==self.boardselect.currentText()]
        for direction,figure,canvas in [('up',self.figure,self.canvas),('down',self.down_figure,self.down_canvas)]:
            draw_sweep(figure,visible,direction,self.plot_title.text(),self.plot_limit(self.rows),color_rows=self.rows,configuration=self.plot_configuration.text() if self.show_configuration.isChecked() else "",dark=True,show_difference=self.show_difference.isChecked(),mode_hint=self.plot_axis_mode())
            canvas.setMinimumHeight(260 * len(figure.axes))
            canvas.draw_idle()
    def closeEvent(self,event):
        if getattr(self,'abort_dialog',None):
            self.abort_dialog.raise_();event.ignore();return
        if self.worker and self.worker.isRunning():
            self.stop();self.log.appendPlainText('Close deferred until acquisition stops and supply is disabled.');event.ignore()
        else:event.accept()


if __name__=='__main__':
    app=QApplication(sys.argv);window=Window();window.show();sys.exit(app.exec())





