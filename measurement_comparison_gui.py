"""Saved-run comparison dialog shared by DC and waveform workflows."""
import json
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QPushButton,QTableWidget,QTableWidgetItem,QHeaderView,QComboBox,QLabel,QFileDialog,QMessageBox,QSpinBox,QDoubleSpinBox,QColorDialog,QScrollArea,QWidget,QFormLayout,QDialogButtonBox
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg,NavigationToolbar2QT
from measurement_comparison import load_measurement,compatible,draw_comparison,series_key
from PyQt6.QtGui import QColor

class MeasurementComparison(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle('Compare saved measurements');self.resize(1400,950);self.runs=[]
        layout=QVBoxLayout(self);buttons=QHBoxLayout();layout.addLayout(buttons)
        for name,fn in [('Add measurements…',self.add_runs),('Remove selected',self.remove_run),('Open comparison…',self.open_comparison),('Export comparison…',self.export),('Line colors…',self.edit_colors)]:
            b=QPushButton(name);b.clicked.connect(fn);buttons.addWidget(b)
        self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['Show','Run label (editable)','Source folder']);self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.table.setMaximumHeight(180);layout.addWidget(self.table);self.table.itemChanged.connect(self.redraw)
        row=QHBoxLayout();layout.addLayout(row);row.addWidget(QLabel('Signal'));self.signal=QComboBox();row.addWidget(self.signal);row.addWidget(QLabel('DC direction'));self.direction=QComboBox();self.direction.addItems(['both','up','down']);row.addWidget(self.direction)
        self.signal.currentIndexChanged.connect(self.redraw);self.direction.currentTextChanged.connect(self.redraw)
        style=QHBoxLayout();layout.addLayout(style)
        self.font_size=QSpinBox();self.font_size.setRange(8,30);self.font_size.setValue(14)
        self.legend_size=QSpinBox();self.legend_size.setRange(6,24);self.legend_size.setValue(11)
        self.line_width=QDoubleSpinBox();self.line_width.setRange(.3,6);self.line_width.setValue(1.8);self.line_width.setSingleStep(.2)
        self.legend_position=QComboBox();self.legend_position.addItems(['best','outside'])
        for label,widget in [('Label size',self.font_size),('Legend size',self.legend_size),('Line width',self.line_width),('Legend position',self.legend_position)]:style.addWidget(QLabel(label));style.addWidget(widget)
        for widget in (self.font_size,self.legend_size,self.line_width):widget.valueChanged.connect(self.redraw)
        self.legend_position.currentTextChanged.connect(self.redraw)
        self.figure=Figure(figsize=(12,7));self.canvas=FigureCanvasQTAgg(self.figure);layout.addWidget(NavigationToolbar2QT(self.canvas,self));layout.addWidget(self.canvas,1);self.redraw()
    def append_run(self,path,label=None,enabled=True,colors=None):
        run=load_measurement(path);compatible(self.runs,run)
        if label is not None:run['label']=label
        for series in run['series']:
            color=(colors or {}).get(series_key(series),'')
            if color and not QColor(color).isValid():raise ValueError('Invalid saved line color')
            if color:series['display_color']=color
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
            if self.table.item(i,0).checkState()==Qt.CheckState.Checked:result.append(dict(r,label=self.table.item(i,1).text(),_style_index=i))
        return result
    def redraw(self,*args):
        draw_comparison(self.figure,self.visible_runs(),self.signal.currentData(),self.direction.currentText(),self.font_size.value(),self.legend_size.value(),self.line_width.value(),self.legend_position.currentText());self.canvas.draw_idle()
    def edit_colors(self):
        dialog=QDialog(self);dialog.setWindowTitle('Comparison line colors');dialog.resize(850,500)
        layout=QVBoxLayout(dialog);scroll=QScrollArea();scroll.setWidgetResizable(True);content=QWidget();form=QFormLayout(content);scroll.setWidget(content);layout.addWidget(scroll)
        for i,run in enumerate(self.runs):
            for series in run['series']:
                row=QHBoxLayout();button=QPushButton(series.get('display_color') or series.get('color') or 'Auto');reset=QPushButton('Reset to signal color');row.addWidget(button);row.addWidget(reset)
                def pick(checked=False,s=series,b=button):
                    color=QColorDialog.getColor(QColor(s.get('display_color') or s.get('color') or '#0072B2'),dialog,'Line color')
                    if color.isValid():s['display_color']=color.name();b.setText(color.name());b.setStyleSheet('color: '+color.name());self.redraw()
                def clear(checked=False,s=series,b=button):
                    s.pop('display_color',None);b.setText(s.get('color') or 'Auto');b.setStyleSheet('');self.redraw()
                button.clicked.connect(pick);reset.clicked.connect(clear)
                form.addRow(f"{self.table.item(i,1).text()} / {series['board']} / {series['signal']} / {series['detail']}",row)
        close=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);close.rejected.connect(dialog.reject);layout.addWidget(close);dialog.exec()

    def export(self):
        if not self.visible_runs():return
        file,_=QFileDialog.getSaveFileName(self,'Save comparison','comparison.png','PNG (*.png)')
        if not file:return
        base=Path(file).with_suffix('')
        try:
            self.figure.savefig(str(base)+'.png',dpi=180);self.figure.savefig(str(base)+'.pdf')
            manifest={'version':1,'runs':[dict(path=r['path'],label=self.table.item(i,1).text(),enabled=self.table.item(i,0).checkState()==Qt.CheckState.Checked,colors={series_key(s):s['display_color'] for s in r['series'] if s.get('display_color')}) for i,r in enumerate(self.runs)],'signal':self.signal.currentData(),'direction':self.direction.currentText(),'appearance':{'font_size':self.font_size.value(),'legend_size':self.legend_size.value(),'line_width':self.line_width.value(),'legend_position':self.legend_position.currentText()}}
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
            for entry in data['runs']:self.append_run(entry['path'],entry['label'],entry.get('enabled',True),entry.get('colors',{}))
            appearance=data.get('appearance',{});self.font_size.setValue(appearance.get('font_size',14));self.legend_size.setValue(appearance.get('legend_size',11));self.line_width.setValue(appearance.get('line_width',1.8));self.legend_position.setCurrentText(appearance.get('legend_position','best'))
            self.signal.setCurrentIndex(max(0,self.signal.findData(data.get('signal'))));self.direction.setCurrentText(data.get('direction','both'));self.redraw()
        except Exception as e:QMessageBox.warning(self,'Could not open comparison',str(e))
