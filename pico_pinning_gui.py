"""Four cable assignments independent of the board marker catalog."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor,QBrush
from PyQt6.QtWidgets import QWidget,QVBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QComboBox,QDoubleSpinBox,QHeaderView

class PicoPinning(QWidget):
    def __init__(self,station):
        super().__init__(station);self.station=station;self.editors={}
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        label=QLabel('Pico probe connections · choose the testpoint physically connected to A–D');layout.addWidget(label)
        self.table=QTableWidget(4,4);self.table.setHorizontalHeaderLabels(['Probe','Connected testpoint','Probe ×','BNC ±range [V]'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.table.setFixedHeight(172);layout.addWidget(self.table)
        for row,ch in enumerate('ABCD'):
            item=QTableWidgetItem(ch);item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable);self.table.setItem(row,0,item)
            combo=QComboBox();probe=QDoubleSpinBox();probe.setDecimals(3);probe.setRange(.001,100000)
            vrange=QComboBox()
            for v in (.01,.02,.05,.1,.2,.5,1,2,5,10,20):vrange.addItem(str(v),v)
            self.editors[ch]=(combo,probe,vrange)
            self.table.setCellWidget(row,1,combo);self.table.setCellWidget(row,2,probe);self.table.setCellWidget(row,3,vrange)
            combo.currentIndexChanged.connect(lambda _,channel=ch:self.assign(channel))
            probe.valueChanged.connect(lambda _,channel=ch:self.settings_changed(channel,3))
            vrange.currentIndexChanged.connect(lambda _,channel=ch:self.settings_changed(channel,4))
    def sync(self):
        s=self.station
        try:mappings=s.read_mapping()
        except ValueError:return
        busy=s.running or (hasattr(s,'measurements_page') and (s.measurements_page.startup_page.is_running() or s.dg4162_page.is_running()))
        self.setVisible(s.backend.currentText()=='pico');self.setEnabled(not busy)
        for ch,(combo,probe,vrange) in self.editors.items():
            for x in (combo,probe,vrange):x.blockSignals(True)
            combo.clear();combo.addItem('Not connected',None)
            for b in s.profile['boards']:
                if b.get('kind')=='connections':continue
                for p in b['testpoints']:
                    combo.addItem(f"{b['id']} / {p['name']}",(b['id'],p['name']))
                    combo.setItemData(combo.count()-1,QBrush(QColor(p.get('color','#9cabbc'))),Qt.ItemDataRole.ForegroundRole)
            m=next((m for m in mappings if m['channel']==ch),None)
            settings=m or s.profile.get('pico_channels',{}).get(ch,{})
            if m:combo.setCurrentIndex(next((i for i in range(combo.count()) if combo.itemData(i)==(m['board'],m['testpoint'])),0))
            probe.setValue(float(settings.get('probe',1)));vrange.setCurrentIndex(max(0,vrange.findData(float(settings.get('range',2)))))
            for x in (combo,probe,vrange):x.blockSignals(False)
    def busy(self):
        s=self.station
        return s.running or (hasattr(s,'measurements_page') and (s.measurements_page.startup_page.is_running() or s.dg4162_page.is_running()))

    def assign(self,ch):
        from testpoint_pinning import assign_probe
        s=self.station
        if self.busy():return
        s.profile['mappings']=s.read_mapping()
        s.profile=assign_probe(s.profile,ch,self.editors[ch][0].currentData())
        s.verified.setChecked(False);s.rebuild()
    def settings_changed(self,ch,col):
        if self.busy():return
        s=self.station;combo,probe,vrange=self.editors[ch]
        value=probe.value() if col==3 else vrange.currentData()
        s.profile.setdefault('pico_channels',{}).setdefault(ch,{})['probe' if col==3 else 'range']=value
        for row in range(s.table.rowCount()):
            if s.table.item(row,2).text()==ch:
                s.table.item(row,col).setText(str(value));break
        s.verified.setChecked(False)
