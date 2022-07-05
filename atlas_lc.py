#!/usr/bin/env python
"""
Author: Sofia Rest
"""

import json, requests, re, time, sys
from collections import OrderedDict
from astropy.time import Time
import numpy as np
from pdastro import pdastrostatsclass, AorB, AnotB

class atlas_lc:
	def __init__(self, tnsname=None, is_averaged=False, mjd_bin_size=None, discdate=None, ra=None, dec=None):
		self.pdastro = pdastrostatsclass()
		self.lcs = {}

		self.tnsname = tnsname
		self.is_averaged = is_averaged
		self.mjd_bin_size = mjd_bin_size
		self.discdate = discdate
		self.ra = ra
		self.dec = dec

		self.corrected_baseline_ix = None
		self.during_sn_ix = None

	def __str__(self):
		res = f'SN {self.tnsname} light curve'
		if self.is_averaged:
			res += f' (averaged with MJD bin size {self.mjd_bin_size})'
		res += f': RA: {self.ra}, Dec: {self.dec}, discovery date: {self.discdate}'
		return res

	# get RA, Dec, and discovery date information from TNS
	def get_tns_data(self, api_key):
		print('Obtaining RA, Dec, and discovery date from TNS...')

		try:
			url = 'https://www.wis-tns.org/api/get/object'
			json_file = OrderedDict([("objname",self.tnsname), ("objid",""), ("photometry","1"), ("spectra","1")])
			data = {'api_key':api_key,'data':json.dumps(json_file)}
			response = requests.post(url, data=data, headers={'User-Agent':'tns_marker{"tns_id":104739,"type": "bot", "name":"Name and Redshift Retriever"}'})
			json_data = json.loads(response.text,object_pairs_hook=OrderedDict)
		except Exception as e:
			raise RuntimeError('# ERROR in get_tns_data(): '+str(e))

		self.ra = json_data['data']['reply']['ra']
		self.dec = json_data['data']['reply']['dec']

		discoverydate = json_data['data']['reply']['discoverydate']
		date = list(discoverydate.partition(' '))[0]
		time = list(discoverydate.partition(' '))[2]
		dateobjects = Time(date+"T"+time, format='isot', scale='utc')
		self.discdate = dateobjects.mjd - 20 # make sure no SN flux before discovery date in baseline indices

		print(f'# RA: {self.ra}, Dec: {self.dec}, discovery date: {self.discdate}')

	# get baseline indices (any indices before the SN discovery date)
	def get_baseline_ix(self):
		if self.discdate is None:
			raise RuntimeError('ERROR: Cannot get baseline indices because discovery date is None!')
		return self.pdastro.ix_inrange(colnames=['MJD'],uplim=self.discdate-20,exclude_uplim=True)

	# get a light curve filename for saving/loading
	def get_filename(self, filt, control_index, directory):
		# SN light curve: 				DIRECTORY/2022xxx/2022xxx.o.lc.txt
		# averaged light curve: 		DIRECTORY/2022xxx/2022xxx.o.1.00days.lc.txt
		# control light curve: 			DIRECTORY/2022xxx/controls/2022xxx_i001.o.lc.txt
		# averaged control light curve: DIRECTORY/2022xxx/controls/2022xxx_i001.o.1.00days.lc.txt

		filename = f'{directory}/{self.tnsname}'
		if control_index != 0:
			filename += '/controls'
		filename += f'/{self.tnsname}'
		if control_index != 0:
			filename += f'_i{control_index:03d}'
		filename += f'.{filt}'
		if self.is_averaged:
			filename += f'.{self.mjd_bin_size:0.2f}days'
		filename += '.lc.txt'
		
		print(f'# Filename: {filename}')
		return filename

	# save SN light curve and, if necessary, control light curves
	def save(self, output_dir, filt=None, overwrite=True):
		print('\nSaving SN light curve...')

		if filt is None:
			o_ix = self.pdastro.ix_equal(colnames=['F'],val='o')
			self.pdastro.write(filename=self.get_filename('o',0,output_dir), indices=o_ix, overwrite=overwrite)
			self.pdastro.write(filename=self.get_filename('c',0,output_dir), indices=AnotB(self.pdastro.getindices(),o_ix), overwrite=overwrite)
		else:
			self.pdastro.write(filename=self.get_filename(filt,0,output_dir), overwrite=overwrite)

		if len(self.lcs) > 0:
			print('Saving control light curves...')
			for control_index in range(1,len(self.lcs)+1):
				if filt is None:
					for filt_ in ['c','o']:
						filt_ix = self.lcs[control_index].ix_equal(colnames=['F'],val=filt_)
						self.lcs[control_index].write(filename=self.get_filename(filt_,control_index,output_dir), indices=filt_ix, overwrite=overwrite)
				else:
					self.lcs[control_index].write(filename=self.get_filename(filt,control_index,output_dir), overwrite=overwrite)

	# load SN light curve and, if necessary, control light curves for a certain filter
	def load(self, filt, input_dir, num_controls=None):
		print('Loading SN light curve...')
		self.pdastro.load_spacesep(self.get_filename(filt,0,input_dir), delim_whitespace=True)

		if not(num_controls is None):
			print(f'Loading {num_controls} control light curves...')
			for control_index in range(1,num_controls+1):
				self.lcs[control_index] = pdastrostatsclass()
				self.lcs[control_index].load_spacesep(self.get_filename(filt,control_index,input_dir), delim_whitespace=True)

	# add downloaded control light curve to control light curve dictionary
	def add_control_lc(self, control_lc):
		self.lcs[len(self.lcs)+1] = control_lc

	# update given indices of 'Mask' column in the SN light curve with given flag(s)
	def update_mask_col(self, flag, indices):
		if len(indices) > 1:
			flag_arr = np.full(self.pdastro.t.loc[indices,'Mask'].shape, flag)
			self.pdastro.t.loc[indices,'Mask'] = np.bitwise_or(self.pdastro.t.loc[indices,'Mask'], flag_arr)
		elif len(indices) == 1:
			self.pdastro.t.loc[indices[0],'Mask'] = int(self.pdastro.t.loc[indices[0],'Mask']) | flag

	# get the xth percentile SN flux using given indices
	def get_xth_percentile_flux(self, percentile, indices=None):
		if indices is None:
			indices = self.pdastro.getindices()
		if len(indices)==0: 
			return None
		else:
			return np.percentile(self.pdastro.t.loc[indices, 'uJy'], percentile)
