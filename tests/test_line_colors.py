"""Tests for optional per-testpoint colors and backwards-compatible board palette."""
import unittest
from matplotlib.figure import Figure
from mapping import default_profile, validate_profile
from sweep_plots import draw_sweep

class LineColorTests(unittest.TestCase):
    def test_profiles_accept_auto_and_explicit_colors(self):
        profile=default_profile()
        self.assertEqual(validate_profile(profile),profile)
        profile['mappings'][0]['color']='#ff0000'
        self.assertEqual(validate_profile(profile)['mappings'][0]['color'],'#ff0000')
        profile['mappings'][0]['color']='not-a-color'
        with self.assertRaisesRegex(ValueError,'Invalid plot color'):
            validate_profile(profile)

    def test_color_override_and_board_default(self):
        rows=[]
        for name,color in [('Vin','#ff0000'),('Vout','')]:
            rows.append(dict(board='board1',testpoint=name,unit='V',direction='up',
                             point=0,current_measured_A=.02,mean=1.7,color=color))
        fig=Figure()
        draw_sweep(fig,rows,'up','Line color test',show_difference=False)
        lines=fig.axes[0].lines
        self.assertEqual(lines[0].get_color(),'#ff0000')
        self.assertNotEqual(lines[1].get_color(),'#ff0000')
        rows[0]['color']=''
        draw_sweep(fig,rows,'up','Board colors',show_difference=False)
        self.assertEqual(fig.axes[0].lines[0].get_color(),
                         fig.axes[0].lines[1].get_color())

    def test_custom_color_same_for_up_and_down(self):
        rows=[dict(board='board1',testpoint='Vref',unit='V',
                   direction=direction,point=0,current_measured_A=.02,
                   mean=1.2,color='#11aa66') for direction in ('up','down')]
        for direction in ('up','down'):
            fig=Figure()
            draw_sweep(fig,rows,direction,'Test',show_difference=False)
            self.assertEqual(fig.axes[0].lines[0].get_color(),'#11aa66')

if __name__=='__main__':
    unittest.main()
