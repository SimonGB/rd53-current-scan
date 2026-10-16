import os
import unittest
os.environ['QT_QPA_PLATFORM']='offscreen'
try:
    from PyQt6.QtWidgets import QApplication
except ImportError:
    QApplication=None

@unittest.skipUnless(QApplication,'Optional GUI dependencies not installed')
class OverviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
    def test_assign_on_other_board_without_switching_view(self):
        from gui import Window
        from board_setup import add_boards,auto_assign
        from venelin import input_profiles
        w=Window();w.backend.setCurrentText('venelin')
        w.profile,_=add_boards(w.profile,4,w.profile['boards'][0])
        w.profile=auto_assign(w.profile,input_profiles(None),[1,2]);w.rebuild()
        self.assertEqual(len(w.overview.cards),5)
        combo=w.overview.cards['board3'].inputs['Vout']
        combo.setCurrentIndex(combo.findData('PMOD3_Ch1'))
        assignment=next(m for m in w.read_mapping() if (m['board'],m['testpoint'])==('board3','Vout'))
        self.assertEqual(assignment['channel'],'PMOD3_Ch1')
        self.assertEqual(w.boardtabs.currentIndex(),0)
        # Choosing an input already assigned to another target is disabled.
        other=w.overview.cards['board1'].inputs['Vout']
        self.assertFalse(other.model().item(other.findData('PMOD3_Ch1')).isEnabled())
        w.close()
    def test_eta_and_live_sweep_styles(self):
        from gui import Window
        from progress_estimate import ProgressEstimate
        w=Window();w.clock=ProgressEstimate(2,.1);w.clock.start(0);w.clock.point(1);w.clock.point(2)
        w.running=True;w.estimate_state='running';w.update_eta();self.assertEqual(w.eta.text(),'Finalizing…')
        w.running=False
        w.rows=[dict(board='board1',testpoint='Vout',unit='V',direction=d,current_measured_A=.01,mean=1.8,capture_std=.001) for d in ('up','down')]
        w.redraw();up=w.figure.axes[0].lines[0];down=w.down_figure.axes[0].lines[0]
        self.assertEqual(up.get_color(),down.get_color());self.assertEqual(down.get_linestyle(),'-')
        self.assertEqual(up.get_marker(),down.get_marker());w.close()
