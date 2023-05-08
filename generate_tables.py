#!/usr/bin/env python
import pandas as pd
import numpy as np
import sys,copy,random
from pdastro import pdastroclass, pdastrostatsclass, AandB, AnotB, AorB
from asym_gaussian import gauss2lc

# plotting
import matplotlib
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pylab as matlib
import warnings
warnings.simplefilter('error', RuntimeWarning)
warnings.filterwarnings("ignore")
plt.rc('axes', titlesize=18)
plt.rc('axes', labelsize=14)
plt.rc('xtick', labelsize=13)
plt.rc('ytick', labelsize=13)
plt.rc('legend', fontsize=13)
plt.rc('font', size=13)
plt.rcParams['font.size'] = 12

# for storing lc and control lcs 
global lcs
lcs = {}

# for storing info about the lc
global lc_info
lc_info = {}

# for storing per-peak tables
global detected
detected = {}

# final table
detected_per_peak = pdastroclass(columns=['gauss_sigma','peak_flux', 'peak_appmag'])

# SN TNS name
tnsname = '2021foa'

# SN discovery date
discovery_date = 59268.45000 - 20.0

# path to directory that contains SN and control light curves
source_dir = '/Users/sofiarest/Desktop/Supernovae/data/misc/2021foa'

# number of control light curves to load
n_controls = 8

# filter of light curve to analyze
filt = 'o'

# MJD bin size in days of light curve to analyze
mjd_bin_size = 1.0

# search for pre-SN bumps with sigmas of gauss_sigmas days
gauss_sigmas = [5, 10, 15, 30]

# select range of peak fluxes to test 
peaks = [2, 5, 7, 10, 13, 15, 17, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150] 

# select possible simulated gaussian widths in days
gauss_widths = [5, 10, 30, 50] # width/2: [2.5, 5, 15, 25]

# number of iterations of random width and peak mjd per peak
iterations = 100000 

# define flag that defines bad measurements (to filter out bad days in lc)
# if more than one, use bitwise OR (this symbol: |) to combine them
flags = 0x800000

# add observation seasons' mjd ranges here
valid_mjd_ranges = [[57406,57568], [57767,57980], [58092,58333], [58491,58705], [58828,59073], [59190,59430], [59560,59831], [59947,60055]]

# optionally add a simulated pre-eruption to the loaded light curve
# then apply a gaussian weighted rolling sum to the SNR
def apply_gaussian(lc, gaussian_sigma, flag=0x800000, sim_gauss=False, sim_peakmjd=None, sim_appmag=None, sim_sigma=None, print_=True):
    if len(lc_info['baseline_ix']) <= 0:
        print('ERROR: Not enough data in baseline flux (before SN start)!')

    #ix = lc.ix_inrange(colnames=['MJDbin'],lowlim=None, uplim=lc_info['discovery_date'])
    ix = lc_info['baseline_ix'] # all pre-SN indices
    #ix = lc.ix_inrange(colnames=['MJDbin'],lowlim=lc.t['MJDbin'].iloc[0], uplim=59000)
    #print(f'# Applying detection to specified MJD bin range: {lc.t["MJDbin"].iloc[ix[0]]} to  {lc.t["MJDbin"].iloc[ix[-1]]}')
    good_ix = AandB(ix, lc.ix_unmasked('Mask', flag)) # all good pre-SN indices

    # make sure there are no lingering simulations
    dropcols=[]
    for col in ['uJysim','SNRsim','simLC','SNRsimsum']:
        if col in lc.t.columns:
            dropcols.append(col)
    if len(dropcols) > 0:
        lc.t.drop(columns=dropcols,inplace=True)

    lc.t.loc[ix, 'SNR'] = 0.0
    lc.t.loc[good_ix,'SNR'] = lc.t.loc[good_ix,'uJy']/lc.t.loc[good_ix,'duJy']

    # add simulated gaussian
    if sim_gauss:
        if print_:
            print(f'# Adding simulated gaussian: peak at MJD {sim_peakmjd:0.2f}; apparent magnitude {sim_appmag:0.2f}; sigma- and sigma+ of {sim_sigma:0.2f} days')
        mjds = lc.t.loc[good_ix,'MJD']
        lc.t.loc[good_ix,'uJysim'] = lc.t.loc[good_ix,'uJy']
        lc.t.loc[ix,'simLC'] = 0.0

        # get simulated gaussian flux and add to light curve flux
        simflux = gauss2lc(mjds, sim_peakmjd, sim_sigma, sim_sigma, app_mag=sim_appmag)
        lc.t.loc[good_ix,'uJysim'] += simflux
        # get the simulated lc for all MJDs
        simflux_all = gauss2lc(lc.t.loc[ix,'MJDbin'], sim_peakmjd, sim_sigma, sim_sigma, app_mag=sim_appmag)
        lc.t.loc[ix,'simLC'] += simflux_all

        # make sure all bad rows have SNRsim = 0.0 so they have no impact on the rolling SNRsum
        lc.t.loc[ix,'SNRsim'] = 0.0
        # include simflux in the SNR
        lc.t.loc[good_ix,'SNRsim'] = lc.t.loc[good_ix,'uJysim']/lc.t.loc[good_ix,'duJy']

    new_gaussian_sigma = round(gaussian_sigma/lc_info['mjd_bin_size'])
    windowsize = int(6 * new_gaussian_sigma)
    halfwindowsize = int(windowsize * 0.5) + 1
    if print_:
        print(f'# Sigma: {gaussian_sigma:0.2f} days; MJD bin size: {lc_info["mjd_bin_size"]:0.2f} days; sigma: {new_gaussian_sigma:0.2f} bins; window size: {windowsize} bins')

    # calculate the rolling SNR sum
    dataindices = np.array(range(len(lc.t.loc[ix])) + np.full(len(lc.t.loc[ix]), halfwindowsize))
    temp = pd.Series(np.zeros(len(lc.t.loc[ix]) + 2*halfwindowsize), name='SNR', dtype=np.float64)
    temp[dataindices] = lc.t.loc[ix,'SNR']
    SNRsum = temp.rolling(windowsize, center=True, win_type='gaussian').sum(std=new_gaussian_sigma)
    lc.t.loc[ix,'SNRsum'] = list(SNRsum[dataindices])
    # normalize it
    norm_temp = pd.Series(np.zeros(len(lc.t.loc[ix]) + 2*halfwindowsize), name='norm', dtype=np.float64)
    norm_temp[np.array(range(len(lc.t.loc[ix])) + np.full(len(lc.t.loc[ix]), halfwindowsize))] = np.ones(len(lc.t.loc[ix]))
    norm_temp_sum = norm_temp.rolling(windowsize, center=True, win_type='gaussian').sum(std=new_gaussian_sigma)
    lc.t.loc[ix,'SNRsumnorm'] = list(SNRsum.loc[dataindices] / norm_temp_sum.loc[dataindices] * max(norm_temp_sum.loc[dataindices]))
    
    # calculate the rolling SNR sum for SNR with simflux
    if sim_gauss:
        temp = pd.Series(np.zeros(len(lc.t.loc[ix]) + 2*halfwindowsize), name='SNRsim', dtype=np.float64)
        temp[dataindices] = lc.t.loc[ix,'SNRsim']
        SNRsimsum = temp.rolling(windowsize, center=True, win_type='gaussian').sum(std=new_gaussian_sigma)
        lc.t.loc[ix,'SNRsimsum'] = list(SNRsimsum.loc[dataindices])

    return lc

def load_lc(source_dir, tnsname):
    lcs[0] = pdastrostatsclass()
    filename = f'{source_dir}/{lc_info["tnsname"]}.{lc_info["filt"]}.{lc_info["mjd_bin_size"]:0.2f}days.lc.txt'
    print(f'\nLoading light curve for SN {tnsname} at {filename}...')
    try:
        lcs[0].load_spacesep(filename,delim_whitespace=True)
    except Exception as e:
        print('ERROR: Could not load light curve at %s: %s' % (filename, str(e)))
        sys.exit()
    
    lc_info['baseline_ix'] = lcs[0].ix_inrange(colnames=['MJDbin'], uplim=lc_info['discovery_date']-20, exclude_uplim=True)
    if len(lc_info['baseline_ix'])<=0:
        print('Baseline length is 0! Exiting...')
        sys.exit()
    lc_info['afterdiscdate_ix'] = AnotB(lcs[0].getindices(), lc_info['baseline_ix']) 

def load_control_lcs(source_dir, n_controls): 
    print(f'\nLoading control light curves for SN {tnsname}...')
    for control_index in range(1, n_controls+1):
        lcs[control_index] = pdastrostatsclass()
        filename = f'{source_dir}/controls/{lc_info["tnsname"]}_i{control_index:03d}.{lc_info["filt"]}.{lc_info["mjd_bin_size"]:0.2f}days.lc.txt'
        print(f'# Loading control light curve {control_index:03d} at {filename}...')
        try:
            lcs[control_index].load_spacesep(filename,delim_whitespace=True)
        except Exception as e:
            print(f'ERROR: Could not load contol light curve {control_index:03d} at {filename}: {str(e)}')
            sys.exit()

def load_lcs(source_dir, tnsname, n_controls):
    load_lc(source_dir, tnsname)
    load_control_lcs(source_dir, n_controls)

def save_detected(gauss_sigma, peak):
    print('Saving...')
    detected[f'{gauss_sigma}_{peak}'].write(filename=f'{source_dir}/bump_analysis/tables2/detected_{gauss_sigma}_{peak:0.2f}.txt')

def save_detected_per_peak(detected_per_peak):
    detected_per_peak.write(filename=f'{source_dir}/bump_analysis/tables2/detected_per_peak.txt')

"""def save_tables(peaks, detected, detected_per_peak):
    for gauss_sigma in gauss_sigmas: 
        for peak in peaks:
            detected[f'{gauss_sigma}_{peak}'].write(filename=f'{source_dir}/bump_analysis/tables2/detected_{gauss_sigma}_{peak:0.2f}.txt')
    detected_per_peak.write(filename=f'{source_dir}/bump_analysis/tables2/detected_per_peak.txt')"""

def save_tables(peaks, detected, detected_per_peak):
    for gauss_sigma in gauss_sigmas: 
        for peak in peaks:
            save_detected(gauss_sigma, peak)
    save_detected_per_peak(detected_per_peak)

def in_season(mjd, valid_mjd_ranges):
    for mjd_range in valid_mjd_ranges:
        if mjd >= mjd_range[0] and mjd <= mjd_range[1]:
            return True
    return False

# fill lc_info and load SN and control lcs
lc_info['tnsname'] = tnsname
lc_info['discovery_date'] = discovery_date
lc_info['filt'] = filt
lc_info['mjd_bin_size'] = mjd_bin_size
load_lcs(source_dir, tnsname, n_controls)

# fill in gauss_sigma column of final table
gauss_sigma_col = np.full(len(peaks), gauss_sigmas[0])
if len(gauss_sigmas) > 1:
    for gauss_sigma_index in range(1, len(gauss_sigmas)):
        a = np.full(len(peaks), gauss_sigmas[gauss_sigma_index])
        gauss_sigma_col = np.concatenate((gauss_sigma_col, a), axis=None)
detected_per_peak.t['gauss_sigma'] = gauss_sigma_col

print(f'\nGaussian sigmas in days: ', gauss_sigmas)
print(f'Peaks in uJy: ', peaks)
print(f'Possible simulated gaussian widths in days: ', gauss_widths)
print(f'Number of iterations per peak: {iterations}')
print(f'Flag for bad days: {hex(flags)}')

gauss_sigma_offset = len(peaks)
for gauss_sigma_index in range(len(gauss_sigmas)):
    gauss_sigma = gauss_sigmas[gauss_sigma_index]
    print(f'\nUsing gaussian sigma of {gauss_sigma} days...')

    for peak_index in range(len(peaks)):
        peak = peaks[peak_index]
        peak_appmag = -2.5 * np.log10(peak) + 23.9  # convert peak flux to peak apparent magnitude

        peak_index += gauss_sigma_index * gauss_sigma_offset
        detected_per_peak.t.loc[peak_index, 'peak_flux'] = peak
        detected_per_peak.t.loc[peak_index, 'peak_appmag'] = peak_appmag
        detected_per_peak.t.loc[peak_index, 'gauss_sigma'] = gauss_sigma

        print(f'Simulating gaussians with peak flux {peak:0.2f} (peak appmag {peak_appmag:0.2f})')

        # initialize per-peak table
        detected[f'{gauss_sigma}_{peak}'] = pdastroclass(columns=['gauss_sigma','peak_flux','peak_appmag','peak_mjd','sigma_days','max_SNRsimsum','detection_limit','detected'])
        detected[f'{gauss_sigma}_{peak}'].t['gauss_sigma'] = np.full(iterations, gauss_sigma)
        detected[f'{gauss_sigma}_{peak}'].t['peak_flux'] = np.full(iterations, peak)
        detected[f'{gauss_sigma}_{peak}'].t['peak_appmag'] = np.full(iterations, peak_appmag)
        
        for i in range(iterations):
            # pick random control light curve
            rand_control_index = random.randrange(1, 8, 1)
            cur_lc = copy.deepcopy(lcs[rand_control_index])

            # select random width in days
            width_days = random.choice(gauss_widths) #random.randrange(gauss_width_min,gauss_width_max+1,1) 
            detected[f'{gauss_sigma}_{peak}'].t.loc[i,'sigma_days'] = width_days/2

            # select random peak MJD from start of lc to 50 days before discovery date
            peak_mjd = random.randrange(cur_lc.t['MJDbin'].iloc[0]-0.5, int(discovery_date)-50, 1) + 0.5
            # make sure peak MJD is within an observation season; else redraw
            while not(in_season(peak_mjd, valid_mjd_ranges)):
                # redraw random peak mjd
                peak_mjd = random.randrange(cur_lc.t['MJDbin'].iloc[0]-0.5, int(discovery_date)-50, 1) + 0.5
            detected[f'{gauss_sigma}_{peak}'].t.loc[i,'peak_mjd'] = peak_mjd

            cur_lc = apply_gaussian(cur_lc, gauss_sigma, flag=flags, sim_gauss=True, sim_peakmjd=peak_mjd, sim_sigma=width_days/2, sim_appmag=peak_appmag, print_=False)

            # compare max SNRsimsum to detection limit 
            # only calculate max SNRsimsum from measurements within 1 sigma of the simulated bump
            sigma_ix = cur_lc.ix_inrange(colnames=['MJD'], lowlim=peak_mjd-(width_days/2), uplim=peak_mjd+(width_days/2), indices=lc_info['baseline_ix'])
            if len(sigma_ix > 0):
                max_snrsimsum_ix = cur_lc.t.loc[sigma_ix,'SNRsimsum'].idxmax()
                detected[f'{gauss_sigma}_{peak}'].t.loc[i,'max_SNRsimsum'] = cur_lc.t.loc[max_snrsimsum_ix,'SNRsimsum']
                detected[f'{gauss_sigma}_{peak}'].t.loc[i,'max_SNRsimsum_mjd'] = cur_lc.t.loc[max_snrsimsum_ix,'MJD']
            else:
                # no valid measurements within this mjd range
                detected[f'{gauss_sigma}_{peak}'].t.loc[i,'max_SNRsimsum'] = np.nan
                detected[f'{gauss_sigma}_{peak}'].t.loc[i,'max_SNRsimsum_mjd'] = np.nan

        save_detected(gauss_sigma, peak)

detected_per_peak.write() 
save_detected_per_peak(detected_per_peak)
#save_tables(peaks, detected, detected_per_peak)
