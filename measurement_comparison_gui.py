"""Saved-run comparison dialog shared by DC and waveform workflows."""
import json
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QPushButton,QTableWidget,QTableWidgetItem,QHeaderView,QComboBox,QLabel,QFileDialog,QMessageBox
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg,NavigationToolbar2QT
from measurement_comparison import load_measurement,compatible,draw_comparison

class MeasurementComparison(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle('Compare saved measurements');self.resize(1200,850);self.runs=[]
        layout=QVBoxLayout(self);buttons=QHBoxLayout();layout.addLayout(buttons)
        for name,fn in [('Add measurements…',self.add_runs),('Remove selected',self.remove_run),('Open comparison…',self.open_comparison),('Export comparison…',self.export)]:
            b=QPushButton(name);b.clicked.connect(fn);buttons.addWidget(b)
        self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['Show','Run label (editable)','Source folder']);self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.table.setMaximumHeight(180);layout.addWidget(self.table);self.table.itemChanged.connect(self.redraw)
        row=QHBoxLayout();layout.addLayout(row);row.addWidget(QLabel('Signal'));self.signal=QComboBox();row.addWidget(self.signal);row.addWidget(QLabel('DC direction'));self.direction=QComboBox();self.direction.addItems(['both','up','down']);row.addWidget(self.direction)
        self.signal.currentIndexChanged.connect(self.redraw);self.direction.currentTextChanged.connect(self.redraw)
        self.figure=Figure(figsize=(10,6));self.canvas=FigureCanvasQTAgg(self.figure);layout.addWidget(NavigationToolbar2QT(self.canvas,self));layout.addWidget(self.canvas,1);self.redraw()
    def append_run(self,path,label=None,enabled=True):
        run=load_measurement(path);compatible(self.runs,run)
        if label is not None:run['label']=label
        self.runs.append(run);i=self.table.rowCount();self.table.blockSignals(True);self.table.insertRow(i)
        show=QTableWidgetItem();show.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable);show.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked);self.table.setItem(i,0,show)
        self.table.setItem(i,1,QTableWidgetItem(run['label']));source=QTableWidgetItem(run['path']);source.setFlags(source.flags() & ~Qt.ItemFlag.ItemIsEditable);self.table.setItem(i,2,source);self.table.blockSignals(False)
        self.refresh_signals();self.redraw()
    def refresh_signals(self):
        old=self.signal.currentData();self.signal.blockSignals(True);self.signal.clear();self.signal.addItem('All signals',None)
        for name in dict.fromkeys(s['signal'] for r in self.runs for s in r['series']):self.signal.addItem(name,name)
        self.signal.setCurrentIndex(max(0,self.signal.findData(old)));self.signal.blockSignals(False)
    def add_runs(self):
        paths,_=QFileDialog.getOpenFileNames(self,'Select metadata.json or measurements.csv files from saved runs','results','Saved runs (metadata.json measurements.csv);;JSON/CSV (*.json *.csv)')
        errors=[]
        for path in paths:
            try:self.append_run(path)
            except Exception as e:errors.append(f'{Path(path).parent.name}: {e}')
        if errors:QMessageBox.warning(self,'Some measurements were not added','\n'.join(errors))
    def remove_run(self):
        i=self.table.currentRow()
        if i<0:return
        self.table.blockSignals(True);self.table.removeRow(i);self.table.blockSignals(False);self.runs.pop(i);self.refresh_signals();self.redraw()
    def visible_runs(self):
        result=[]
        for i,r in enumerate(self.runs):
            if self.table.item(i,0).checkState()==Qt.CheckState.Checked:result.append(dict(r,label=self.table.item(i,1).text()))
        return result
    def redraw(self,*args):
        draw_comparison(self.figure,self.visible_runs(),self.signal.currentData(),self.direction.currentText());self.canvas.draw_idle()
    def export(self):
        if not self.visible_runs():return
        file,_=QFileDialog.getSaveFileName(self,'Save comparison','comparison.png','PNG (*.png)')
        if not file:return
        base=Path(file).with_suffix('')
        try:
            self.figure.savefig(str(base)+'.png',dpi=180);self.figure.savefig(str(base)+'.pdf')
            manifest={'version':1,'runs':[dict(path=r['path'],label=self.table.item(i,1).text(),enabled=self.table.item(i,0).checkState()==Qt.CheckState.Checked) for i,r in enumerate(self.runs)],'signal':self.signal.currentData(),'direction':self.direction.currentText()}
            Path(str(base)+'.json').write_text(json.dumps(manifest,indent=2)+'\n')
        except Exception as e:QMessageBox.warning(self,'Export failed',str(e))
    def open_comparison(self):
        file,_=QFileDialog.getOpenFileName(self,'Open saved comparison','','JSON (*.json)')
        if not file:return
        try:
            data=json.loads(Path(file).read_text());loaded=[]
            for entry in data['runs']:
                run=load_measurement(entry['path']);compatible(loaded,run);loaded.append(run)
            self.table.blockSignals(True);self.table.setRowCount(0);self.table.blockSignals(False);self.runs=[]
            for entry in data['runs']:self.append_run(entry['path'],entry['label'],entry.get('enabled',True))
            self.signal.setCurrentIndex(max(0,self.signal.findData(data.get('signal'))));self.direction.setCurrentText(data.get('direction','both'));self.redraw()
        except Exception as e:QMessageBox.warning(self,'Could not open comparison',str(e))
