# SN 2023ixf Light Curve Cleaning and Averaging

The ATLAS SN light curves are separated by filter (orange and cyan) and labelled as such in the file name. Averaged light curves contain an additional number in the file name that represents the MJD bin size used. Control light curves are located in the "controls" subdirectory and follow the same naming scheme, only with their control index added after the SN name.

The following details the file names for each of the light curve versions:
	- SN light curves: 2023ixf.o.lc.txt and 2023ixf.c.lc.txt
	- Averaged light curves: 2023ixf.o.1.00days.lc.txt and 2023ixf.c.1.00days.lc.txt
	- Control light curves, where X=001,...,016: 2023ixf_iX.o.lc.txt and 2023ixf_iX.c.lc.txt

The following summarizes the hex values in the "Mask" column of each light curve for each cut applied (see below sections for more information on each cut): 
	- Uncertainty cut: 0x2
	- Chi-square cut: 0x1
	- Control light curve cut: 0x400000
	- Bad day (for averaged light curves): 0x800000

## FILTER: o

### Uncertainty cut
Total percent of data flagged (0x2): 5.26%

### True uncertainties estimation
We can increase the typical uncertainties from 19.00 to 21.90 by adding an additional systematic uncertainty of 10.89 in quadrature
New typical uncertainty is 15.27% greater than old typical uncertainty
Apply true uncertainties estimation set to True
True uncertainties estimation recommended
Applying procedure...
The extra noise was added to the uncertainties of the SN light curve and copied to the "duJy_new" column

### Chi-square cut
Chi-square cut 10.00 selected with 3.24% contamination and 0.16% loss
Total percent of data flagged (0x1): 2.48%

### Control light curve cut
Percent of data above x2_max bound (0x100): 0.15%
Percent of data above stn_max bound (0x200): 0.10%
Percent of data above Nclip_max bound (0x400): 0.83%
Percent of data below Ngood_min bound (0x800): 2.79%
Total percent of data flagged as questionable (not masked with control light curve flags but Nclip > 0) (0x80000): 43.55%
Total percent of data flagged as bad (0x400000): 3.72%

After the cuts are applied, the light curves are resaved with the new "Mask" column.
Total percent of data flagged as bad (0xc00003): 8.26

### Averaging cleaned light curves
Total percent of binned data flagged (0x800000): 6.06%
The averaged light curves are then saved in a new file with the MJD bin size added to the filename.

## FILTER: c

### Uncertainty cut
Total percent of data flagged (0x2): 6.10%

### True uncertainties estimation
We can increase the typical uncertainties from 13.00 to 15.50 by adding an additional systematic uncertainty of 8.45 in quadrature
New typical uncertainty is 19.25% greater than old typical uncertainty
Apply true uncertainties estimation set to True
True uncertainties estimation recommended
Applying procedure...
The extra noise was added to the uncertainties of the SN light curve and copied to the "duJy_new" column

### Chi-square cut
Chi-square cut 10.00 selected with 3.61% contamination and 0.29% loss
Total percent of data flagged (0x1): 5.11%

### Control light curve cut
Percent of data above x2_max bound (0x100): 0.33%
Percent of data above stn_max bound (0x200): 0.16%
Percent of data above Nclip_max bound (0x400): 0.66%
Percent of data below Ngood_min bound (0x800): 0.82%
Total percent of data flagged as questionable (not masked with control light curve flags but Nclip > 0) (0x80000): 50.74%
Total percent of data flagged as bad (0x400000): 1.81%

After the cuts are applied, the light curves are resaved with the new "Mask" column.
Total percent of data flagged as bad (0xc00003): 12.36

### Averaging cleaned light curves
Total percent of binned data flagged (0x800000): 11.43%
The averaged light curves are then saved in a new file with the MJD bin size added to the filename.