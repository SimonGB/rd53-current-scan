import copy
from pathlib import Path
import tempfile
import threading
import unittest
import numpy as np
from board_setup import add_boards,auto_assign,delete_testpoint,active_profile
from mapping import default_profile,validate_profile
from venelin import input_profiles,Venelin

class BoardSetupTests(unittest.TestCase):
    def test_five_boards_type_aware_assignment(self):
        p=default_profile();p['backend']='venelin';p['mappings']=[]
        p,ids=add_boards(p,4)
        self.assertEqual(ids,['board2','board3','board4','board5'])
        p=auto_assign(p,input_profiles(None),[1,2]);validate_profile(p)
        self.assertEqual(len(p['mappings']),10)
        self.assertEqual(len({m['channel'] for m in p['mappings']}),10)
        self.assertTrue(all(input_profiles(None)[m['channel']]['kind']=='dV' for m in p['mappings']))
        q=delete_testpoint(p,'board2','Vref')
        self.assertEqual(len(q['mappings']),9)
        self.assertEqual(len(p['mappings']),10)
        self.assertEqual(len(q['boards'][0]['testpoints']),2)
        q['boards'][0]['enabled']=False
        self.assertEqual(len(active_profile(q)['boards']),4)
        self.assertTrue(all(m['board']!='board1' for m in active_profile(q)['mappings']))
    def test_no_partial_mutation_when_inputs_exhausted(self):
        p=default_profile();p['backend']='venelin';p['mappings']=[];p,_=add_boards(p,4)
        original=copy.deepcopy(p)
        with self.assertRaises(ValueError):auto_assign(p,input_profiles(None),[1])
        self.assertEqual(p,original)
    def test_batch_capture_uses_one_helper_and_keeps_windows(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=Path(d)/'config.toml';cfg.write_text('[Calibration]\nPrecisionBits=24\n')
            data=Path(d)/'fadc.dat';r=Venelin(d,cfg,data,['PMOD1_Ch1'],1000,.002,threading.Event());calls=[]
            def command(name,args,timeout):
                calls.append((name,args))
                rows=['# ADC_CH1(PMOD1_dV1)']+[f'{i} {hex(i)} {4194304 if i<2 else 2097152}' for i in range(6)]
                data.write_text('\n'.join(rows)+'\n')
            r.command=command;captures=r.capture_many(3)
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0][1][calls[0][1].index('--nsamples')+1],'6')
            self.assertEqual(len(captures),3)
            np.testing.assert_allclose(captures[0]['PMOD1_Ch1'],[1.8,1.8])
            np.testing.assert_allclose(captures[1]['PMOD1_Ch1'],[.9,.9])
            self.assertEqual(len(captures[2]['PMOD1_Ch1']),2)

    def test_local_thesis_figure_extraction(self):
        try:
            import fitz
            from PIL import Image
        except ImportError:self.skipTest('Optional PDF import dependencies not installed')
        import io
        from thesis_import import candidates
        with tempfile.TemporaryDirectory() as d:
            stream=io.BytesIO();Image.new('RGB',(400,220),'green').save(stream,format='PNG')
            pdf=fitz.open();page=pdf.new_page();page.insert_text((30,30),'Testplatine regulator test board')
            page.insert_image(fitz.Rect(30,60,430,280),stream=stream.getvalue());path=Path(d)/'thesis.pdf';pdf.save(path);pdf.close()
            figures=candidates(path)
            self.assertEqual(len(figures),1);self.assertEqual(figures[0]['width'],400)
            self.assertEqual(figures[0]['page'],1);self.assertTrue(figures[0]['relevant'])
