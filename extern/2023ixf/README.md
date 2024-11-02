# SN 2023ixf Light Curve Cleaning and Averaging

The ATLAS SN light curves are separated by filter (orange and cyan) and labelled as such in the file name. Averaged light curves contain an additional number in the file name that represents the MJD bin size used. Control light curves are located in the "controls" subdirectory and follow the same naming scheme, only with their control index added after the SN name.

The following details the file names for each of the light curve versions:
	- SN light curves: 2023ixf.o.lc.txt and 2023ixf.c.lc.txt
	- Averaged light curves: 2023ixf.o.1.00days.lc.txt and 2023ixf.c.1.00days.lc.txt

The following summarizes the hex values in the "Mask" column of each light curve for each cut applied (see below sections for more information on each cut): 
	- Bad day (for averaged light curves): 0x800000

## FILTER: o

### True uncertainties estimation
We can increase the typical uncertainties from 19.00 to 21.90 by adding an additional systematic uncertainty of 10.89 in quadrature
New typical uncertainty is 15.27% greater than old typical uncertainty
Apply true uncertainties estimation set to False
True uncertainties estimation recommended
Skipping procedure...

After the cuts are applied, the light curves are resaved with the new "Mask" column.
Total percent of data flagged as bad (0xc00003): 8.26

### Averaging cleaned light curves
Total percent of binned data flagged (0x800000): 6.06%
The averaged light curves are then saved in a new file with the MJD bin size added to the filename.

## FILTER: c

### True uncertainties estimation
We can increase the typical uncertainties from 13.00 to 15.50 by adding an additional systematic uncertainty of 8.45 in quadrature
New typical uncertainty is 19.25% greater than old typical uncertainty
Apply true uncertainties estimation set to False
True uncertainties estimation recommended
Skipping procedure...

After the cuts are applied, the light curves are resaved with the new "Mask" column.
Total percent of data flagged as bad (0xc00003): 12.36

### Averaging cleaned light curves
Total percent of binned data flagged (0x800000): 11.43%
The averaged light curves are then saved in a new file with the MJD bin size added to the filename.