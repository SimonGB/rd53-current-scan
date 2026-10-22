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

    def test_main_gui_mode_navigation_preserves_mapping(self):
        from gui import Window
        window=Window()
        try:
            self.assertEqual(window.mode_select.count(),2)
            self.assertEqual(window.station_tabs.count(),3)
            self.assertEqual(window.station_tabs.tabText(0),'1 · Setup boards & testpoints')
            self.assertEqual(window.station_tabs.tabText(1),'2 · Measurements')
            self.assertEqual(window.station_tabs.tabText(2),'3 · Results & plots')
            self.assertEqual(window.measure_pages.currentIndex(),0)
            original_mapping=window.profile
            window.mode_select.setCurrentIndex(1)
            self.assertEqual(window.measure_pages.currentIndex(),1)
            self.assertIsNotNone(window.dg4162_page)
            self.assertTrue(window.dg4162_page.measure.isChecked())
            self.assertEqual(window.dg_measurement_profile()['backend'],'pico')
            self.assertFalse(window.dg4162_page.enable.isChecked())
            window.mode_select.setCurrentIndex(0)
            self.assertEqual(window.measure_pages.currentIndex(),0)
            self.assertIs(window.profile,original_mapping)
        finally:
            window.close()


if __name__=='__main__':
    unittest.main()
