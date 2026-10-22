"""Fundamental-frequency transfer analysis for simultaneous PicoScope waveforms.

Fit a sinusoid + DC at the known DG4162 frequency. Square-wave drive is
handled by extracting only its fundamental, not by using total Vpp.
All amplitudes below are *peak* amplitudes, so the ratio is independent
of whether peak or RMS amplitude would otherwise be used.
"""
from __future__ import annotations

import math
import numpy as np


def fundamental(samples, sample_interval_s, frequency_hz):
    """Estimate the complex voltage phasor (peak) for a waveform.

    Model: V(t) = DC + real(A * exp(j*2π*f*t)).
    Reject underresolved captures instead of reporting misleading gain.
    """
    y=np.asarray(samples,dtype=float)
    if y.ndim!=1 or y.size<16 or not np.isfinite(y).all():
        raise ValueError('Invalid or insufficient waveform samples')
    if not (math.isfinite(sample_interval_s) and sample_interval_s>0 and
            math.isfinite(frequency_hz) and frequency_hz>0):
        raise ValueError('Invalid sample interval or modulation frequency')
    samples_per_cycle=1.0/(sample_interval_s*frequency_hz)
    covered_cycles=y.size/samples_per_cycle
    if samples_per_cycle<8:
        raise ValueError(f'Insufficient time resolution ({samples_per_cycle:.1f} samples/cycle; need >= 8)')
    if covered_cycles<1.5:
        raise ValueError(f'Capture only spans {covered_cycles:.2f} cycles; need >= 1.5 cycles')

    # Taking t relative to the start of the *simultaneously sampled* input
    # and output is essential for the relative phase measurement.
    angle=2*math.pi*frequency_hz*sample_interval_s*np.arange(y.size)
    design=np.column_stack([np.ones(y.size),np.cos(angle),np.sin(angle)])
    coefficients=np.linalg.lstsq(design,y,rcond=None)[0]
    predicted=design@coefficients
    residual=float(np.sqrt(np.mean((y-predicted)**2)))
    phasor=complex(coefficients[1],-coefficients[2])
    return dict(phasor=phasor,amplitude_vpk=abs(phasor),
                dc_v=float(coefficients[0]),residual_rms_v=residual,
                cycles=covered_cycles,samples_per_cycle=samples_per_cycle)


def voltage_transfer(vin,vout,sample_interval_s,frequency_hz,min_input_vpk=.001):
    """Return fundamental Vout/Vin gain, attenuation, and relative phase.

    Attenuation [dB] = 20log10(|Vin_fundamental|/|Vout_fundamental|).
    This is a voltage-transfer attenuation, NOT an intrinsic PSRR
    measurement unless the experimental injection/reference is appropriate.
    """
    incoming=fundamental(vin,sample_interval_s,frequency_hz)
    outgoing=fundamental(vout,sample_interval_s,frequency_hz)
    if not (np.isfinite(min_input_vpk) and min_input_vpk>0):
        raise ValueError('Minimum input amplitude must be positive and finite')
    # A square-wave drive contains substantial *real harmonics*. Those
    # harmonics appear in the one-frequency fit residual and must not be
    # misclassified as broadband noise. Use a conservative absolute
    # fundamental-amplitude floor; show the fit residual separately.
    threshold=min_input_vpk
    if incoming['amplitude_vpk']<=threshold:
        raise ValueError(
            f'Vin fundamental {incoming["amplitude_vpk"]:.4g} Vpk is '
            f'below signal/noise threshold {threshold:.4g} Vpk')
    # Very small, noise-like Vout cannot yield a reliable phase.
    if outgoing['amplitude_vpk']<=1e-5:
        raise ValueError('Vout fundamental is below 10 µVpk; phase is undefined')
    transfer=outgoing['phasor']/incoming['phasor']
    gain_db=float(20*np.log10(abs(transfer)))
    phase=float(np.degrees(np.angle(transfer)))
    return dict(gain_db=gain_db,attenuation_db=-gain_db,
                phase_deg=phase,vin_fundamental_vpk=incoming['amplitude_vpk'],
                vout_fundamental_vpk=outgoing['amplitude_vpk'],
                vin_fit_residual_rms_v=incoming['residual_rms_v'],
                vout_fit_residual_rms_v=outgoing['residual_rms_v'],
                covered_cycles=incoming['cycles'])
