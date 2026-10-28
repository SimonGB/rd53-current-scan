"""Side-by-side board cards with direct input assignment."""
from PyQt6.QtCore import Qt,pyqtSignal
from PyQt6.QtWidgets import QWidget,QScrollArea,QVBoxLayout,QHBoxLayout,QLabel,QCheckBox,QComboBox,QSizePolicy


class BoardCard(QWidget):
    assigned=pyqtSignal(str,str,str)
    enabled=pyqtSignal(str,bool)
    focused=pyqtSignal(str,str)
    placed=pyqtSignal(str,float,float)
    def __init__(self,board,view_factory):
        super().__init__();self.board=board;self.setFixedWidth(290 if board.get('kind')=='connections' else 180)
        layout=QVBoxLayout(self);layout.setContentsMargins(7,5,7,5);layout.setSpacing(3)
        self.include=QCheckBox(board['id']);self.include.setToolTip('Include this board in the scan')
        self.include.toggled.connect(lambda value:self.enabled.emit(self.board['id'],value));layout.addWidget(self.include)
        self.view=view_factory();self.view.compact=True;self.view.board=board;self.view.setFixedHeight(112);layout.addWidget(self.view)
        self.view.clicked.connect(self.focus_point)
        self.view.place.connect(lambda x,y:self.placed.emit(self.board['id'],x,y))
        if board.get('kind')=='connections':self.view.hide()
        self.inputs={};self.labels={}
        for point in board['testpoints']:
            row=QHBoxLayout();label=QLabel(point['name']);label.setFixedWidth(155 if board.get('kind')=='connections' else 42);label.setWordWrap(True);label.setToolTip(point['name'])
            combo=QComboBox();combo.setMinimumWidth(0);combo.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
            combo.setToolTip('Choose the input physically wired to this testpoint')
            combo.currentIndexChanged.connect(lambda _,name=point['name'],c=combo:self.assigned.emit(self.board['id'],name,c.currentData() or ''))
            self.labels[point['name']]=label;self.inputs[point['name']]=combo;row.addWidget(label);row.addWidget(combo,1);layout.addLayout(row)
        self.status=QLabel('Not measured');self.status.setWordWrap(True);self.status.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);layout.addWidget(self.status)
        layout.addStretch();self.setStyleSheet('BoardCard {border:1px solid #35566e;border-radius:8px;background:#102334;}')
    def focus_point(self,name):
        self.focused.emit(self.board['id'],name)
        if name in self.inputs:
            combo=self.inputs[name];combo.setFocus()
            if combo.isEnabled() and combo.isVisible():combo.showPopup()
    def sync(self,board,mappings,choices,running,edit,selected):
        self.board=board;self.include.blockSignals(True);self.include.setChecked(board.get('enabled',True));self.include.blockSignals(False);self.include.setEnabled(not running)
        self.view.board=board;self.view.mappings=mappings;self.view.edit=edit and not running
        self.view.selected=selected if selected in self.inputs else '';self.view.update()
        assignments={(m['board'],m['testpoint']):m['channel'] for m in mappings}
        used={m['channel']:(m['board'],m['testpoint']) for m in mappings}
        for point,combo in self.inputs.items():
            color=next((p.get('color','') for p in board['testpoints'] if p['name']==point),'')
            self.labels[point].setStyleSheet('color: '+color if color else '')
            combo.setVisible(not (choices and all(c in 'ABCD' and len(c)==1 for c,_ in choices)))
            current=assignments.get((board['id'],point),'')
            combo.blockSignals(True);combo.clear();combo.addItem('Not assigned','')
            available={c for c,_ in choices}
            if current and current not in available:combo.addItem(current,current)
            for channel,label in choices:
                combo.addItem(label,channel)
                if channel in used and used[channel]!=(board['id'],point):
                    item=combo.model().item(combo.count()-1);item.setEnabled(False);item.setToolTip('Assigned to '+' / '.join(used[channel]))
            combo.setCurrentIndex(max(0,combo.findData(current)));combo.blockSignals(False)
            combo.setEnabled(not running and board.get('enabled',True))
            combo.setToolTip(dict(choices).get(current,current) or 'Choose a measurement input')


class BoardOverview(QScrollArea):
    assigned=pyqtSignal(str,str,str)
    enabled=pyqtSignal(str,bool)
    focused=pyqtSignal(str,str)
    placed=pyqtSignal(str,float,float)
    def __init__(self,view_factory):
        super().__init__();self.view_factory=view_factory;self.cards={};self.signature=None
        self.setWidgetResizable(True);self.setMinimumHeight(235);self.setMaximumHeight(350)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)
    def sync(self,boards,mappings,choices,running=False,edit=False,selected_board='',selected_point=''):
        signature=tuple((b['id'],b.get('image',''),tuple((p['name'],p['x'],p['y']) for p in b['testpoints'])) for b in boards)
        if signature!=self.signature:
            old=self.takeWidget()
            if old is not None:old.deleteLater()
            content=QWidget();layout=QHBoxLayout(content);layout.setContentsMargins(5,5,5,5);layout.setSpacing(7);layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
            self.cards={}
            for board in boards:
                card=BoardCard(board,self.view_factory);self.cards[board['id']]=card
                card.assigned.connect(self.assigned);card.enabled.connect(self.enabled);card.focused.connect(self.focused);card.placed.connect(self.placed)
                layout.addWidget(card)
            self.setWidget(content);self.signature=signature
        for b in boards:self.cards[b['id']].sync(b,mappings,choices,running,edit,selected_point if b['id']==selected_board else '')
    def readings(self,batch):
        for board,card in self.cards.items():
            rows=[r for r in batch if r['board']==board]
            if rows:card.status.setText(' · '.join(f"{r['testpoint']} {r['mean']:.4g} {r['unit']}" for r in rows))

