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
            self.assertEqual(window.station_tabs.count(),5)
            self.assertIn('Instruments',window.station_tabs.tabText(0))
            self.assertIn('Boards',window.station_tabs.tabText(1))
            self.assertIn('Measurements',window.station_tabs.tabText(2))
            self.assertIn('Live',window.station_tabs.tabText(3))
            self.assertIn('Analysis',window.station_tabs.tabText(4))
            self.assertEqual(window.station_tabs.currentIndex(),0)
            self.assertTrue(window.station_tabs.widget(1).findChildren(QSplitter))
            original_profile=window.profile
            original_board_tabs=window.boardtabs
            original_figure=window.figure
            # Instrument connection settings are genuinely shared.
            window.instruments_page.hmp_port.setText('/dev/hmp4040-test')
            self.assertEqual(window.port.text(),'/dev/hmp4040-test')
            window.instruments_page.rigol_resource.setText('/dev/usbtmc42')
            self.assertEqual(window.dg4162_page.resource.currentText(),'/dev/usbtmc42')
            window.station_tabs.setCurrentIndex(2)
            self.assertEqual(window.station_tabs.currentIndex(),2)
            self.assertEqual(window.measurements_page.modes.count(),2)
            self.assertIs(window.measurements_page.modes.widget(1),
                          window.dg4162_page)
            window.measurements_page.dc_panel.fields['start'].setValue(12)
            self.assertAlmostEqual(window.fields['start'].value(),12)
            window.fields['start'].setValue(18)
            self.assertAlmostEqual(window.measurements_page.dc_panel.fields['start'].value(),18)
            self.assertTrue(window.dg4162_page.measure.isChecked())
            self.assertEqual(window.dg_measurement_profile()['backend'],'pico')
            self.assertIn('attenuation',window.dg4162_page.plot_quantity.itemText(3).lower())
            window.station_tabs.setCurrentIndex(3)
            self.assertEqual(window.station_tabs.currentIndex(),3)
            window.station_tabs.setCurrentIndex(1)
            self.assertIs(window.profile,original_profile)
            self.assertIs(window.boardtabs,original_board_tabs)
            self.assertIs(window.figure,original_figure)
        finally:
            window.close()

    def test_rigol_plot_and_settling_notifications_do_not_crash(self):
        from dg4162_gui import DG4162Page
        page=DG4162Page()
        try:
            page.on_step_started(0,25,1.0,20.0)
            self.assertIn('SETTLING',page.status_label.text())
            page.on_progress({'index':0,'readback_hz':1.0,'dwell_s':20.,
                              'board1_Vin_mean_v':1.1,
                              'board1_Vout_mean_v':1.8,
                              'board1_attenuation_db':20.0},25)
            self.assertEqual(page.run_progress.value(),4)
            self.assertEqual(len(page.live_points),1)
            page.plot_quantity.setCurrentIndex(3)
            self.assertIn('MEASURING',page.status_label.text())
        finally:
            page.close()

    def test_dashboard_start_of_step_is_not_a_completed_point(self):
        from workflow_pages import LiveDashboard
        page=LiveDashboard()
        try:
            page.start_dg()
            page.update_dg({'_phase':'settling','index':0,
                            'readback_hz':1.0,'dwell_s':20.0},25)
            self.assertEqual(page.dg_rows,[])
            self.assertIn('settling',page.status.text())
            page.update_dg({'index':0,'readback_hz':1.0,'dwell_s':20.0,
                            'board1_Vin_mean_v':1.1},25)
            self.assertEqual(len(page.dg_rows),1)
            self.assertEqual(page.progress.value(),4)
        finally:
            page.close()


if __name__=='__main__':
    unittest.main()
