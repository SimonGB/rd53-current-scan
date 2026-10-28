import unittest
from mapping import default_profile
from testpoint_pinning import initialize_colors,assign_probe,point_color

class TestpointPinningTests(unittest.TestCase):
    def test_color_survives_disconnect_and_move_to_another_channel(self):
        p=initialize_colors(default_profile());b=p['boards'][0];b['testpoints'].append({'name':'Vin','x':.1,'y':.2,'color':'#ff0000'})
        p['mappings'][0]['probe']=10;p['mappings'][0]['range']=.5
        p=assign_probe(p,'A',(b['id'],'Vin'))
        self.assertEqual(p['mappings'][-1]['color'],'#ff0000');self.assertEqual(p['mappings'][-1]['probe'],10)
        p=assign_probe(p,'A',None);self.assertEqual(point_color(p,b['id'],'Vin'),'#ff0000')
        p=assign_probe(p,'C',(b['id'],'Vin'));self.assertEqual(p['mappings'][-1]['color'],'#ff0000');self.assertEqual(p['mappings'][-1]['probe'],1)
        p=assign_probe(p,'A',(b['id'],'Vref'));self.assertEqual(p['mappings'][-1]['probe'],10)
    def test_move_existing_target_is_unique_and_preserves_factors(self):
        p=default_profile();p['mappings'][1]['probe']=10
        p=assign_probe(p,'A',('board1','Vout'))
        self.assertEqual(len(p['mappings']),1);self.assertEqual(p['pico_channels']['B']['probe'],10)
        p=assign_probe(p,'B',('board1','Vref'));self.assertEqual(p['mappings'][-1]['probe'],10)
    def test_existing_custom_color_migrates_and_original_not_mutated(self):
        p=default_profile();p['mappings'][0]['color']='#123456'
        result=assign_probe(p,'A',None)
        self.assertEqual(point_color(result,'board1','Vref'),'#123456');self.assertNotIn('color',p['boards'][0]['testpoints'][0])

import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
try:
    from PyQt6.QtWidgets import QApplication
except ImportError:QApplication=None

@unittest.skipUnless(QApplication,'Optional GUI dependencies')
class QuickPicoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_dropdown_replaces_assignment_with_persistent_marker_color(self):
        from gui import Window
        w=Window()
        try:
            w.profile['boards'][0]['testpoints'].append(dict(name='Vin',x=.1,y=.2,color='#ff0000'));w.rebuild()
            combo,probe,vrange=w.pico_pinning.editors['A'];probe.setValue(10)
            combo.setCurrentIndex(combo.findText('board1 / Vin'))
            m=next(m for m in w.read_mapping() if m['channel']=='A')
            self.assertEqual(m['testpoint'],'Vin');self.assertEqual(m['probe'],10);self.assertEqual(m['color'],'#ff0000')
            combo.setCurrentIndex(0)
            self.assertEqual(point_color(w.profile,'board1','Vin'),'#ff0000')
            combo.setCurrentIndex(combo.findText('board1 / Vin'))
            self.assertEqual(next(m for m in w.read_mapping() if m['channel']=='A')['probe'],10)
            self.assertEqual(len(w.snapshot()['boards'][0]['testpoints']),3)
        finally:w.close()
