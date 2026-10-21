"""Shared non-destructive plot-style editor for archived measurements."""
from PyQt6.QtWidgets import (
    QDialog, QFormLayout, QComboBox, QPushButton, QColorDialog,
    QDialogButtonBox, QLabel,
)
from PyQt6.QtGui import QColor

LINE_STYLES = [
    ('Auto', ''), ('Solid', '-'), ('Dashed', '--'), ('Dotted', ':'),
    ('Dash-dot', '-.'), ('No line', 'None'),
]
MARKERS = [
    ('Auto', ''), ('Circle', 'o'), ('Square', 's'), ('Triangle up', '^'),
    ('Triangle down', 'v'), ('Diamond', 'D'), ('Cross', 'x'),
    ('Plus', '+'), ('Star', '*'), ('Point', '.'), ('No marker', 'None'),
]
STYLE_KEYS = ('color', 'linestyle', 'marker')


def edit_style(parent, testpoint, existing):
    """Return style dict, or None when cancelled. Empty fields mean Auto."""
    old = {key: existing.get(key, '') or '' for key in STYLE_KEYS}
    dialog = QDialog(parent)
    dialog.setWindowTitle('Plotting · ' + str(testpoint))
    form = QFormLayout(dialog)

    mode = QComboBox()
    mode.addItems(['Auto (board and signal defaults)', 'Custom'])
    mode.setCurrentIndex(int(any(old.values())))
    form.addRow('Style', mode)

    chosen = [old['color']]
    button = QPushButton(chosen[0] or 'Choose color…')

    def choose_color():
        initial = QColor(chosen[0]) if chosen[0] else QColor('#56B4E9')
        picked = QColorDialog.getColor(initial, dialog, 'Line color')
        if picked.isValid():
            chosen[0] = picked.name()
            button.setText(chosen[0])

    button.clicked.connect(choose_color)
    form.addRow('Color', button)
    line = QComboBox()
    for label, value in LINE_STYLES:
        line.addItem(label, value)
    line.setCurrentIndex(max(0, line.findData(old['linestyle'])))
    form.addRow('Line style', line)

    marker = QComboBox()
    for label, value in MARKERS:
        marker.addItem(label, value)
    marker.setCurrentIndex(max(0, marker.findData(old['marker'])))
    form.addRow('Symbol', marker)
    note = QLabel('Individual Custom settings left at Auto retain their original defaults.')
    note.setWordWrap(True)
    form.addRow(note)

    def toggle():
        enabled = mode.currentIndex() == 1
        for widget in (button, line, marker):
            widget.setEnabled(enabled)

    mode.currentIndexChanged.connect(toggle)
    toggle()
    actions = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    actions.accepted.connect(dialog.accept)
    actions.rejected.connect(dialog.reject)
    form.addRow(actions)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    if mode.currentIndex() == 0:
        return dict.fromkeys(STYLE_KEYS, '')
    return {'color': chosen[0], 'linestyle': line.currentData(), 'marker': marker.currentData()}
