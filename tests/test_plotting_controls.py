"""Plotting editor and axis maximum regressions."""
import unittest
from matplotlib.figure import Figure
from mapping import default_profile, validate_profile
from sweep_plots import draw_sweep


class CustomLineStyles(unittest.TestCase):
    def test_auto_and_custom_profile_validation(self):
        profile=default_profile()
        validate_profile(profile)
        profile['mappings'][0].update(color='#e34735',linestyle='--',marker='D')
        validate_profile(profile)
        profile['mappings'][0]['marker']='invalid'
        with self.assertRaisesRegex(ValueError,'Invalid marker'):
            validate_profile(profile)

    def test_style_override_and_old_default(self):
        base=dict(board='b1',testpoint='Vin',unit='V',direction='up',
                  point=0,current_measured_A=.02,mean=1.3)
        fig=Figure()
        draw_sweep(fig,[base],'up','Default',show_difference=False)
        line=fig.axes[0].lines[0]
        self.assertEqual(line.get_linestyle(),':')
        self.assertEqual(line.get_marker(),'o')
        custom=dict(base,color='#f53b40',linestyle='--',marker='D')
        draw_sweep(fig,[custom],'up','Custom',show_difference=False)
        line=fig.axes[0].lines[0]
        self.assertEqual(line.get_linestyle(),'--')
        self.assertEqual(line.get_marker(),'D')
        self.assertEqual(line.get_color(),'#f53b40')

    def test_custom_style_stable_up_down(self):
        rows=[dict(board='b1',testpoint='Vout',unit='V',direction=direction,
                   point=0,current_measured_A=.02,mean=1.7,linestyle='-.',
                   marker='s',color='#1255ae') for direction in ('up','down')]
        for direction in ('up','down'):
            fig=Figure()
            draw_sweep(fig,rows,direction,'Check',show_difference=False)
            line=fig.axes[0].lines[0]
            self.assertEqual((line.get_linestyle(),line.get_marker(),line.get_color()),
                             ('-.','s','#1255ae'))


import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
try:
    from PyQt6.QtWidgets import QApplication
except ImportError:
    QApplication=None


@unittest.skipUnless(QApplication,'GUI requires optional PyQt6')
class PlottingGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_single_maximum_control_and_mode_memories(self):
        from gui import Window
        w=Window()
        try:
            self.assertFalse(hasattr(w,'plot_max_v'))
            initial=w.scan_form.getWidgetPosition(w.plot_max)[0]
            w.plot_max.setValue(85)
            w.sweep_mode.setCurrentText('Voltage')
            self.assertEqual(w.scan_form.getWidgetPosition(w.plot_max)[0],initial)
            self.assertEqual(w.scan_form.labelForField(w.plot_max).text(),'Plot maximum [V]')
            self.assertEqual(w.plot_max.value(),2.0)
            w.plot_max.setValue(1.75)
            w.sweep_mode.setCurrentText('Current')
            self.assertEqual(w.plot_max.value(),85)
            w.sweep_mode.setCurrentText('Voltage')
            self.assertEqual(w.plot_max.value(),1.75)
        finally:
            w.close()

    def test_old_wiring_defaults_to_auto(self):
        from gui import Window
        from PyQt6.QtCore import Qt
        w=Window()
        try:
            self.assertEqual(w.table.horizontalHeaderItem(5).text(),'Plotting')
            self.assertEqual(w.table.item(0,5).text(),'Auto')
            self.assertEqual(w.read_mapping()[0]['linestyle'],'')
            self.assertEqual(w.read_mapping()[0]['marker'],'')
            w.table.item(0,5).setData(Qt.ItemDataRole.UserRole,
                                     dict(color='#ff0000',linestyle=':',marker='s'))
            mapping=w.read_mapping()
            self.assertEqual(mapping[0]['color'],'#ff0000')
            self.assertEqual(mapping[0]['linestyle'],':')
            self.assertEqual(mapping[0]['marker'],'s')
        finally:
            w.close()


if __name__=='__main__':
    unittest.main()
