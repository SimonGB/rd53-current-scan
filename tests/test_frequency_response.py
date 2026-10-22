"""Frequency response/phase math on simultaneous synthetic Pico captures."""
import math
import unittest
import numpy as np

from frequency_response import fundamental,voltage_transfer


class TransferTests(unittest.TestCase):
    def test_attenuation_and_phase_from_sinusoidal_capture(self):
        frequency=2000
        dt=1/100000
        time=np.arange(4000)*dt
        vin=1.1+.1*np.sin(2*np.pi*frequency*time)
        # 20 dB voltage attenuation, 45-degree output phase lag.
        vout=1.8+.01*np.sin(2*np.pi*frequency*time-np.pi/4)
        result=voltage_transfer(vin,vout,dt,frequency)
        self.assertAlmostEqual(result['gain_db'],-20,places=4)
        self.assertAlmostEqual(result['attenuation_db'],20,places=4)
        self.assertAlmostEqual(result['phase_deg'],-45,places=3)
        self.assertAlmostEqual(result['vin_fundamental_vpk'],.1,places=4)

    def test_square_wave_uses_fundamental_not_total_peak_to_peak(self):
        frequency=1000
        dt=1/100000
        time=np.arange(5000)*dt
        fundamental_sine=np.sin(2*np.pi*frequency*time)
        vin=.2*np.sign(fundamental_sine)+1
        # Linear model: fundamental passes at 1/4 magnitude, 90° lag.
        # Same 3rd harmonic suppressed entirely on Vout.
        vout=1.8+.2*(4/np.pi)*.25*np.sin(2*np.pi*frequency*time-np.pi/2)
        result=voltage_transfer(vin,vout,dt,frequency)
        self.assertAlmostEqual(result['attenuation_db'],20*math.log10(4),delta=.2)
        self.assertAlmostEqual(result['phase_deg'],-90,delta=2)

    def test_reject_underresolved_capture(self):
        with self.assertRaisesRegex(ValueError,'Capture only spans'):
            fundamental(np.ones(300),1e-4,1)
        with self.assertRaisesRegex(ValueError,'Insufficient time resolution'):
            fundamental(np.ones(100),1e-3,200)

    def test_reject_inadequate_input_ripple(self):
        f=1000;dt=1e-5;t=np.arange(2000)*dt
        with self.assertRaisesRegex(ValueError,'Vin fundamental'):
            voltage_transfer(1+1e-5*np.sin(2*np.pi*f*t),
                             1.8+.01*np.sin(2*np.pi*f*t),dt,f)


if __name__=='__main__':
    unittest.main()
