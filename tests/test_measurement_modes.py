"""Qt smoke tests for measurement-mode selection and DG4162 page safety."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest


class TestMeasurementModes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PyQt6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest('PyQt6 not installed')
        cls.app=QApplication.instance() or QApplication([])

    def test_dg_page_preview_without_resource(self):
        from dg4162_gui import DG4162Page
        page=DG4162Page()
        try:
            self.assertFalse(page.enable.isChecked())
            page.preview_plan()
            self.assertIn('Preview 25',page.log.toPlainText())
            self.assertFalse(page.stop.isEnabled())
        finally:
            page.close()

    def test_main_gui_tabs_preserve_original_splitter_and_mapping(self):
        from gui import Window
        from PyQt6.QtWidgets import QSplitter
        window=Window()
        try:
            self.assertEqual(window.station_tabs.count(),3)
            self.assertIn('Boards',window.station_tabs.tabText(0))
            self.assertIn('Rigol',window.station_tabs.tabText(1))
            self.assertIn('Analysis',window.station_tabs.tabText(2))
            self.assertEqual(window.station_tabs.currentIndex(),0)
            self.assertTrue(window.station_tabs.widget(0).findChildren(QSplitter))
            original_profile=window.profile
            original_board_tabs=window.boardtabs
            original_figure=window.figure
            window.station_tabs.setCurrentIndex(1)
            self.assertEqual(window.station_tabs.currentIndex(),1)
            self.assertTrue(window.dg4162_page.measure.isChecked())
            self.assertEqual(window.dg_measurement_profile()['backend'],'pico')
            self.assertIn('attenuation',window.dg4162_page.plot_quantity.itemText(3).lower())
            window.station_tabs.setCurrentIndex(0)
            self.assertEqual(window.station_tabs.currentIndex(),0)
            self.assertIs(window.profile,original_profile)
            self.assertIs(window.boardtabs,original_board_tabs)
            self.assertIs(window.figure,original_figure)
        finally:
            window.close()


if __name__=='__main__':
    unittest.main()
