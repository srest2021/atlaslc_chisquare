#!/usr/bin/env python

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Tuple, Type
import re, json, requests, time, sys, io
from astropy import units as u
from astropy.coordinates import Angle
from astropy.time import Time
from collections import OrderedDict
from pdastro import pdastrostatsclass
import numpy as np
import pandas as pd
from copy import deepcopy
from pathlib import Path

# number of days to subtract from TNS discovery date to make sure no SN flux before discovery date
DISC_DATE_BUFFER = 20

# required light curve column names for the script to work
REQUIRED_COLUMN_NAMES = ["MJD", "uJy", "duJy"]

# required averaged light curve column names for the script to work
REQUIRED_AVG_COLUMN_NAMES = ["MJDbin", "uJy", "duJy", "Mask"]

ATLAS_FILTERS = ["c", "o"]

DEFAULT_CUT_NAMES = ["uncert_cut", "x2_cut", "controls_cut", "badday_cut", "averaging"]

"""
UTILITY
"""


def AandB(A, B):
    return np.intersect1d(A, B, assume_unique=False)


def AnotB(A, B):
    return np.setdiff1d(A, B)


def AorB(A, B):
    return np.union1d(A, B)


def not_AandB(A, B):
    return np.setxor1d(A, B)


class Credentials:
    def __init__(
        self, atlas_username, atlas_password, tns_api_key, tns_id, tns_bot_name
    ):
        self.atlas_username = None if atlas_username == "None" else atlas_username
        self.atlas_password = None if atlas_password == "None" else atlas_password
        self.tns_api_key = None if tns_api_key == "None" else tns_api_key
        self.tns_id = None if tns_id == "None" else tns_id
        self.tns_bot_name = None if tns_bot_name == "None" else tns_bot_name

        tns_params = [self.tns_api_key, self.tns_id, self.tns_bot_name]
        not_none_count = sum(param is not None for param in tns_params)
        if 0 < not_none_count < 3:
            raise RuntimeError(
                "Either all or none of 'tns_api_key', 'tns_id', and 'tns_bot_name' must be provided."
            )


class RA:
    def __init__(self, string=None):
        self.angle = None
        if string:
            self.set_angle(string)

    def set_angle(self, string):
        s = re.compile("\:")
        if isinstance(string, str) and s.search(string):
            A = Angle(string, u.hour)
        else:
            A = Angle(string, u.degree)
        self.angle: Angle = A


class Dec:
    def __init__(self, string=None):
        self.angle = None
        if string:
            self.set_angle(string)

    def set_angle(self, string):
        self.angle: Angle = Angle(string, u.degree)


class Coordinates:
    def __init__(self, ra: str | None = None, dec: str | None = None):
        self.ra: RA = RA(ra)
        self.dec: Dec = Dec(dec)

    def set_RA(self, ra):
        self.ra = RA(ra)

    def set_Dec(self, dec):
        self.dec = Dec(dec)

    def is_empty(self) -> bool:
        return self.ra.angle is None or self.dec.angle is None

    def __str__(self):
        if self.is_empty():
            raise RuntimeError(f"ERROR: Coordinates are empty and cannot be printed.")
        return f"RA {self.ra.angle.degree:0.14f}, Dec {self.dec.angle.degree:0.14f}"


def get_filename(
    output_dir, tnsname, filt="o", control_index=0, mjdbinsize=None, cleaned=False
):
    filename = f"{output_dir}/{tnsname}"

    if control_index != 0:
        filename += "/controls"

    filename += f"/{tnsname}"

    if control_index != 0:
        filename += f"_i{control_index:03d}"

    filename += f".{filt}"

    if mjdbinsize:
        filename += f".{mjdbinsize:0.2f}days"

    if cleaned:
        filename += f".clean"

    filename += ".lc.txt"
    return filename


def query_tns(tnsname, api_key, tns_id, bot_name):
    if tns_id is None or bot_name is None:
        print(
            "WARNING: Cannot query TNS without TNS ID and bot name. Please specify these parameters in config.ini."
        )
        return None

    try:
        url = "https://www.wis-tns.org/api/get/object"
        json_file = OrderedDict(
            [("objname", tnsname), ("objid", ""), ("photometry", "1"), ("spectra", "1")]
        )
        data = {"api_key": api_key, "data": json.dumps(json_file)}
        response = requests.post(
            url,
            data=data,
            headers={
                "User-Agent": 'tns_marker{"tns_id":"%s","type": "bot", "name":"%s"}'
                % (tns_id, bot_name)
            },
        )
        json_data = json.loads(response.text, object_pairs_hook=OrderedDict)
        return json_data
    except Exception as e:
        print(json_data["data"]["reply"])
        raise RuntimeError("ERROR in query_tns(): " + str(e))


def get_tns_coords_from_json(json_data):
    try:
        coords = Coordinates(
            json_data["data"]["reply"]["ra"], json_data["data"]["reply"]["dec"]
        )
        return coords
    except Exception as e:
        raise RuntimeError(
            f"ERROR: Failed to get coordinates from TNS JSON data: {str(e)}"
        )


def get_tns_mjd0_from_json(json_data):
    try:
        disc_date = json_data["data"]["reply"]["discoverydate"]
        date = list(disc_date.partition(" "))[0]
        time = list(disc_date.partition(" "))[2]
        date_object = Time(date + "T" + time, format="isot", scale="utc")
        mjd0 = date_object.mjd - DISC_DATE_BUFFER
        return mjd0
    except Exception as e:
        raise RuntimeError(
            f"ERROR: Failed to get discovery date from TNS JSON data: {str(e)}"
        )


def query_atlas(headers, ra, dec, min_mjd, max_mjd):
    baseurl = "https://fallingstar-data.com/forcedphot"
    task_url = None
    while not task_url:
        with requests.Session() as s:
            resp = s.post(
                f"{baseurl}/queue/",
                headers=headers,
                data={
                    "ra": ra,
                    "dec": dec,
                    "send_email": False,
                    "mjd_min": min_mjd,
                    "mjd_max": max_mjd,
                },
            )
            if resp.status_code == 201:
                task_url = resp.json()["url"]
                print(f"Task url: {task_url}")
            elif resp.status_code == 429:
                message = resp.json()["detail"]
                print(f"{resp.status_code} {message}")
                t_sec = re.findall(r"available in (\d+) seconds", message)
                t_min = re.findall(r"available in (\d+) minutes", message)
                if t_sec:
                    waittime = int(t_sec[0])
                elif t_min:
                    waittime = int(t_min[0]) * 60
                else:
                    waittime = 10
                print(f"Waiting {waittime} seconds")
                time.sleep(waittime)
            else:
                print(f"ERROR {resp.status_code}")
                print(resp.text)
                sys.exit()

    result_url = None
    taskstarted_printed = False

    print("Waiting for job to start...")
    while not result_url:
        with requests.Session() as s:
            resp = s.get(task_url, headers=headers)
            if resp.status_code == 200:
                if not (resp.json()["finishtimestamp"] is None):
                    result_url = resp.json()["result_url"]
                    print(f"Task is complete with results available at {result_url}")
                    break
                elif resp.json()["starttimestamp"]:
                    if not taskstarted_printed:
                        print(
                            f"Task is running (started at {resp.json()['starttimestamp']})"
                        )
                        taskstarted_printed = True
                    time.sleep(2)
                else:
                    # print(f"Waiting for job to start (queued at {resp.json()['timestamp']})")
                    time.sleep(4)
            else:
                print(f"ERROR {resp.status_code}")
                print(resp.text)
                sys.exit()

    with requests.Session() as s:
        if result_url is None:
            print("WARNING: Empty light curve (no data within this MJD range).")
            dfresult = pd.DataFrame(
                columns=[
                    "MJD",
                    "m",
                    "dm",
                    "uJy",
                    "duJy",
                    "F",
                    "err",
                    "chi/N",
                    "RA",
                    "Dec",
                    "x",
                    "y",
                    "maj",
                    "min",
                    "phi",
                    "apfit",
                    "Sky",
                    "ZP",
                    "Obs",
                    "Mask",
                ]
            )
        else:
            result = s.get(result_url, headers=headers).text
            dfresult = pd.read_csv(
                io.StringIO(result.replace("###", "")), delim_whitespace=True
            )

    return dfresult


# input/output table containing TNS names, RA, Dec, and MJD0
# (TODO: if MJD0=None, consider entire light curve as pre-SN light curve)
class SnInfoTable:
    def __init__(self, directory, filename=None):
        if filename is None:
            self.filename = f"{directory}/sninfo.txt"
        else:
            self.filename = f"{directory}/{filename}"

        try:
            print(f"Loading SN info table at {self.filename}...")
            self.t = pd.read_table(self.filename, delim_whitespace=True)
            if not "tnsname" in self.t.columns:
                raise RuntimeError('ERROR: SN info table must have a "tnsname" column.')
            self.t["ra"] = self.t["ra"].astype(str)
            self.t["dec"] = self.t["dec"].astype(str)
            print("Success")
        except Exception:
            print(f"No existing SN info table at that path; creating blank table...")
            self.t = pd.DataFrame(
                columns=["tnsname", "ra", "dec", "mjd0"]
            )  # , 'closebright_ra', 'closebright_dec'])

    def get_row(self, tnsname):
        if self.t.empty:
            # raise RuntimeError(f'Error: Cannot get info for SN {tnsname}--table is empty.')
            return -1, None

        matching_ix = np.where(self.t["tnsname"].eq(tnsname))[0]
        if len(matching_ix) >= 2:
            print(
                f"WARNING: SN info table has {len(matching_ix)} matching rows for TNS name {tnsname}. Dropping duplicate rows..."
            )
            self.t.drop(matching_ix[1:], inplace=True)
            return matching_ix[0], self.t.loc[matching_ix[0], :]
        elif len(matching_ix) == 1:
            return matching_ix[0], self.t.loc[matching_ix[0], :]
        else:
            # raise RuntimeError(f'Error: Cannot get info for SN {tnsname}--row doesn\'t exist.')
            return -1, None

    def is_nan(self, string: str):
        return string.lower() == "nan"

    def get_info(self, tnsname):
        _, row = self.get_row(tnsname)
        if row is None:
            return None, None, None

        # coords = Coordinates(row['ra'], row['dec'])
        ra = None if self.is_nan(row["ra"]) else row["ra"]
        dec = None if self.is_nan(row["dec"]) else row["dec"]

        if np.isnan(row["mjd0"]):
            mjd0 = None
        else:
            if not isinstance(row["mjd0"], (int, float)):
                raise RuntimeError(f'ERROR: Invalid MJD0: {row["mjd0"]}')
            mjd0 = float(row["mjd0"])

        return ra, dec, mjd0

    def update_row_at_index(
        self, index, coords: Coordinates = None, mjd0: float = None, overwrite=False
    ):
        try:
            if overwrite or np.isnan(self.t.loc[index, "mjd0"]):
                self.t.loc[index, "mjd0"] = mjd0

            if (
                (overwrite or self.is_nan(self.t.loc[index, "ra"]))
                and not coords is None
                and not coords.is_empty()
            ):
                self.t.loc[index, "ra"] = f"{coords.ra.angle.degree:0.14f}"

            if (
                (overwrite or self.is_nan(self.t.loc[index, "dec"]))
                and not coords is None
                and not coords.is_empty()
            ):
                self.t.loc[index, "dec"] = f"{coords.dec.angle.degree:0.14f}"
        except Exception as e:
            raise RuntimeError(
                f"ERROR: Could not update SN info table at index {index}: {str(e)}"
            )

    def add_new_row(self, tnsname, coords: Coordinates = None, mjd0: float = None):
        if mjd0 is None:
            mjd0 = np.nan

        ra = np.nan
        dec = np.nan
        if not coords is None and not coords.is_empty():
            ra = f"{coords.ra.angle.degree:0.14f}"
            dec = f"{coords.dec.angle.degree:0.14f}"

        row = {"tnsname": tnsname, "ra": ra, "dec": dec, "mjd0": mjd0}
        self.t = pd.concat([self.t, pd.DataFrame([row])], ignore_index=True)

    def update_row(
        self, tnsname, coords: Coordinates = None, mjd0: float = None, overwrite=False
    ):
        if self.t.empty:
            self.add_new_row(tnsname, coords, mjd0)
            return

        matching_ix = np.where(self.t["tnsname"].eq(tnsname))[0]
        if len(matching_ix) > 1:
            raise RuntimeError(
                f"ERROR: SN info table has {len(matching_ix)} matching rows for TNS name {tnsname}."
            )
        elif len(matching_ix) == 1:
            index = matching_ix[0]
            self.update_row_at_index(
                index, coords=coords, mjd0=mjd0, overwrite=overwrite
            )
        else:
            self.add_new_row(tnsname, coords, mjd0)

    def save(self):
        print(f"\nSaving SN info table at {self.filename}...")
        self.t["ra"] = self.t["ra"].astype(str)
        self.t["dec"] = self.t["dec"].astype(str)
        self.t.to_string(self.filename, index=False)
        print("Success")

    def __str__(self):
        return self.t.to_string()


def get_mjd0(
    tnsname: str, sninfo: SnInfoTable, credentials: Credentials
) -> Tuple[float, Coordinates | None]:
    _, sninfo_row = sninfo.get_row(tnsname)
    if not sninfo_row is None and not np.isnan(sninfo_row["mjd0"]):
        # get MJD0 from SN info table
        print(f'\nSetting MJD0 to {sninfo_row["mjd0"]} MJD from SN info table...')
        mjd0 = float(sninfo_row["mjd0"])
        if not isinstance(mjd0, (int, float)):
            raise RuntimeError(f"ERROR: Invalid MJD0: {mjd0}")
        else:
            print("Success")
            return mjd0, None
    else:
        # get MJD0 from TNS
        print(f"\nQuerying TNS for SN {tnsname} discovery date...")
        json_data = query_tns(
            tnsname,
            credentials.tns_api_key,
            credentials.tns_id,
            credentials.tns_bot_name,
        )
        mjd0 = get_tns_mjd0_from_json(json_data)
        coords = get_tns_coords_from_json(json_data)
        return mjd0, coords


class Cut:
    def __init__(
        self,
        column: str = None,
        min_value: float = None,
        max_value: float = None,
        flag: int = None,
        params: Dict[str, Any] = None,
    ):
        self.column = column
        self.min_value = min_value
        self.max_value = max_value
        self.flag = flag
        self.params = params

    def can_apply_directly(self):
        if (
            not self.flag
            or not self.column
            or (not self.min_value and not self.max_value)
        ):
            return False
        return True

    def __str__(self):
        output = ""
        if self.column:
            output += f"column={self.column} "
        if self.flag:
            output += f"flag={hex(self.flag)} "
        if self.min_value:
            output += f"min_value={self.min_value} "
        if self.max_value:
            output += f"max_value={self.max_value}"
        return output


class CutList:
    def __init__(self):
        self.list: Dict[str, Type[Cut]] = {}

    def add(self, cut: Cut, name: str):
        if name in self.list:
            raise RuntimeError(f"ERROR: cut by the name {name} already exists.")
        self.list[name] = cut

    def get(self, name: str):
        if not name in self.list:
            return None
        return self.list[name]

    def remove(self, names: str | List[str]):
        if isinstance(names, str):
            if self.has(name):
                del self.list[names]
        else:
            for name in names:
                if self.has(name):
                    del self.list[name]

    def has(self, name: str):
        return name in self.list

    def can_apply_directly(self, name: str):
        return self.list[name].can_apply_directly()

    def check_for_flag_duplicates(self):
        if len(self.list) < 1:
            return

        unique_flags = set()
        duplicate_flags = []

        for name, cut in self.list.items():
            if name == "uncert_est":
                continue

            flags = [cut.flag]
            if cut.params:
                for key in cut.params:
                    if key.endswith("_flag"):
                        flags.append(cut.params[key])

            for flag in flags:
                if flag in unique_flags:
                    if not flag is None:
                        duplicate_flags.append(flag)
                elif not flag is None:
                    unique_flags.add(flag)

        return len(duplicate_flags) > 0, duplicate_flags

    def get_custom_cuts(self) -> Dict[str, Cut]:
        custom_cuts = {}

        for name in self.list:
            if not name in DEFAULT_CUT_NAMES and name != "uncert_est":
                custom_cuts[name] = self.list[name]

        return custom_cuts

    def get_all_flags(self):
        mask = 0
        for name in self.list:
            if name != "uncert_est":
                mask = mask | self.list[name].flag
        return mask

    def get_previous_flags(self, current_cut_name: str):
        skip_names: List = (
            [
                "uncert_est",
                "badday_cut",
            ]
            + list(self.get_custom_cuts().values())
            + [
                "controls_cut",
                "x2_cut",
                "uncert_cut",
            ]
        )

        try:
            current_cut_index = skip_names.index(current_cut_name)
        except Exception as e:
            raise RuntimeError(
                f"ERROR: Cannot get previous flags for a custom cut: {str(e)}"
            )
        skip_names = skip_names[: current_cut_index + 1]
        mask = 0
        for name in self.list:
            if not name in skip_names:
                mask = mask | self.list[name].flag
        return mask

    def __str__(self):
        output = ""
        for name in self.list:
            output += f"\n{name}: " + self.list[name].__str__()
        return output


class LimCutsTable:
    def __init__(self, lc: pdastrostatsclass, stn_bound, indices=None):
        self.t = None

        self.lc = lc
        if indices is None:
            indices = self.lc.getindices()
        self.indices = indices

        self.good_ix, self.bad_ix = self.get_goodbad_indices(stn_bound)

    def get_goodbad_indices(self, stn_bound):
        good_ix = self.lc.ix_inrange(
            colnames=["uJy/duJy"],
            lowlim=-stn_bound,
            uplim=stn_bound,
            indices=self.indices,
        )
        bad_ix = AnotB(self.indices, good_ix)
        return good_ix, bad_ix

    def get_keptcut_indices(self, x2_max):
        kept_ix = self.lc.ix_inrange(
            colnames=["chi/N"], uplim=x2_max, indices=self.indices
        )
        cut_ix = AnotB(self.indices, kept_ix)
        return kept_ix, cut_ix

    def calculate_row(self, x2_max, kept_ix=None, cut_ix=None):
        if kept_ix is None or cut_ix is None:
            kept_ix, cut_ix = self.get_keptcut_indices(x2_max)
        data = {
            "PSF Chi-Square Cut": x2_max,
            "N": len(self.indices),
            "Ngood": len(self.good_ix),
            "Nbad": len(self.bad_ix),
            "Nkept": len(kept_ix),
            "Ncut": len(cut_ix),
            "Ngood,kept": len(AandB(self.good_ix, kept_ix)),
            "Ngood,cut": len(AandB(self.good_ix, cut_ix)),
            "Nbad,kept": len(AandB(self.bad_ix, kept_ix)),
            "Nbad,cut": len(AandB(self.bad_ix, cut_ix)),
            "Pgood,kept": 100 * len(AandB(self.good_ix, kept_ix)) / len(self.indices),
            "Pgood,cut": 100 * len(AandB(self.good_ix, cut_ix)) / len(self.indices),
            "Pbad,kept": 100 * len(AandB(self.bad_ix, kept_ix)) / len(self.indices),
            "Pbad,cut": 100 * len(AandB(self.bad_ix, cut_ix)) / len(self.indices),
            "Ngood,kept/Ngood": 100
            * len(AandB(self.good_ix, kept_ix))
            / len(self.good_ix),
            "Ploss": 100 * len(AandB(self.good_ix, cut_ix)) / len(self.good_ix),
            "Pcontamination": 100 * len(AandB(self.bad_ix, kept_ix)) / len(kept_ix),
        }
        return data

    def calculate_table(self, cut_start, cut_stop, cut_step):
        print(
            f"Calculating loss and contamination for chi-square cuts from {cut_start} to {cut_stop}..."
        )

        self.t = pd.DataFrame(
            columns=[
                "PSF Chi-Square Cut",
                "N",
                "Ngood",
                "Nbad",
                "Nkept",
                "Ncut",
                "Ngood,kept",
                "Ngood,cut",
                "Nbad,kept",
                "Nbad,cut",
                "Pgood,kept",
                "Pgood,cut",
                "Pbad,kept",
                "Pbad,cut",
                "Ngood,kept/Ngood",
                "Ploss",
                "Pcontamination",
            ]
        )

        # for different x2 cuts decreasing from 50
        for cut in range(cut_start, cut_stop + 1, cut_step):
            kept_ix, cut_ix = self.get_keptcut_indices(cut)
            percent_kept = 100 * len(kept_ix) / len(self.indices)
            if percent_kept < 10:
                # less than 10% of measurements kept, so no chi-square cuts beyond this point are valid
                continue
            row = self.calculate_row(cut, kept_ix=kept_ix, cut_ix=cut_ix)
            self.t = pd.concat([self.t, pd.DataFrame([row])], ignore_index=True)


"""
LIGHT CURVES
"""


class Supernova:
    def __init__(
        self,
        tnsname: str = None,
        ra: str = None,
        dec: str = None,
        mjd0: float = None,
        filt="o",
    ):
        self.tnsname = tnsname
        self.coords: Coordinates = Coordinates(ra, dec)
        self.mjd0 = mjd0
        self.filt = filt

        self.lcs: Dict[int, LightCurve] = {}

        self.num_controls = 0
        self.all_indices = None
        self.control_indices = None

    def get(self, control_index=0):
        try:
            return self.lcs[control_index].t
        except:
            raise RuntimeError(
                f"ERROR: Cannot get control light curve {control_index}. Num controls set to {self.num_controls} and {len(self.lcs)} lcs in dictionary."
            )

    def get_tns_data(self, api_key, tns_id, bot_name):
        if self.coords.is_empty() or self.mjd0 is None:
            print(f"\nQuerying TNS for {self.tnsname} data...")
            json_data = query_tns(self.tnsname, api_key, tns_id, bot_name)
            if json_data is None:
                print(f"Skipping...")
                return

            if self.coords.is_empty():
                self.coords = get_tns_coords_from_json(json_data)

            if self.mjd0 is None:
                self.mjd0 = get_tns_mjd0_from_json(json_data)

            print("Success")

    def verify_mjds(self, verbose=False):
        # sort SN lc by MJD
        self.lcs[0].t.sort_values(by=["MJD"], ignore_index=True, inplace=True)

        if self.num_controls == 0:
            return

        if verbose:
            print("\nMaking sure SN and control light curve MJDs match up exactly:")

        sn_sorted_mjd = self.lcs[0].t["MJD"].to_numpy()

        for control_index in self.get_control_indices():
            # sort by MJD
            self.lcs[control_index].t.sort_values(
                by=["MJD"], ignore_index=True, inplace=True
            )
            control_sorted_mjd = self.lcs[control_index].t["MJD"].to_numpy()

            if (len(sn_sorted_mjd) != len(control_sorted_mjd)) or not np.array_equal(
                sn_sorted_mjd, control_sorted_mjd
            ):
                if verbose:
                    print(
                        f"MJDs out of agreement for control light curve {control_index}, fixing..."
                    )

                only_sn_mjd = AnotB(sn_sorted_mjd, control_sorted_mjd)
                only_control_mjd = AnotB(control_sorted_mjd, sn_sorted_mjd)

                # for the MJDs only in SN, add row with that MJD to control light curve,
                # with all values of other columns NaN
                if len(only_sn_mjd) > 0:
                    for mjd in only_sn_mjd:
                        self.lcs[control_index].newrow({"MJD": mjd, "Mask": 0})

                # remove indices of rows in control light curve for which there is no MJD in the SN lc
                if len(only_control_mjd) > 0:
                    ix_to_skip = []
                    for mjd in only_control_mjd:
                        matching_ix = self.lcs[control_index].ix_equal("MJD", mjd)
                        if len(matching_ix) != 1:
                            raise RuntimeError(
                                f"ERROR: Couldn't find MJD={mjd} in column MJD, but should be there!"
                            )
                        ix_to_skip.extend(matching_ix)
                    ix = AnotB(self.lcs[control_index].getindices(), ix_to_skip)
                else:
                    ix = self.lcs[control_index].getindices()

                # sort again
                sorted_ix = self.lcs[control_index].ix_sort_by_cols("MJD", indices=ix)
                self.lcs[control_index].t = self.lcs[control_index].t.loc[sorted_ix]

            self.lcs[control_index].t.reset_index(drop=True, inplace=True)

        print("Success")

    def prep_for_cleaning(self, verbose=False):
        if verbose:
            print(
                'Adding blank "Mask" columns, replacing infs with NaNs, and calculating flux/dflux...'
            )

        for control_index in self.get_all_indices():
            # add blank 'Mask' column
            self.lcs[control_index].t["Mask"] = 0
            # remove rows with duJy=0 or uJy=NaN
            self.lcs[control_index].remove_invalid_rows()
            # calculate flux/dflux column
            self.lcs[control_index].calculate_fdf_column()
        print("Success")

        # make sure SN and control lc MJDs match up exactly
        self.verify_mjds(verbose=verbose)

    def apply_cut(self, cut: Cut):
        if not cut.can_apply_directly():
            raise RuntimeError(f"ERROR: Cannot directly apply the following cut: {cut}")

        sn_percent_cut = None
        for control_index in self.get_all_indices():
            percent_cut = self.lcs[control_index].apply_cut(
                cut.column, cut.flag, min_value=cut.min_value, max_value=cut.max_value
            )
            if control_index == 0:
                sn_percent_cut = percent_cut

        return sn_percent_cut

    def get_uncert_est_stats(self, cut: Cut):
        def get_sigma_extra(median_dflux, stdev):
            return max(0, np.sqrt(stdev**2 - median_dflux**2))

        stats = pd.DataFrame(
            columns=["control_index", "median_dflux", "stdev", "sigma_extra"]
        )
        stats["control_index"] = self.get_control_indices()
        stats.set_index("control_index", inplace=True)

        for control_index in self.get_control_indices():
            dflux_clean_ix = self.lcs[control_index].ix_unmasked(
                "Mask", maskval=cut.params["uncert_cut_flag"]
            )
            x2_clean_ix = self.lcs[control_index].ix_inrange(
                colnames=["chi/N"],
                uplim=cut.params["temp_x2_max_value"],
                exclude_uplim=True,
            )
            clean_ix = AandB(dflux_clean_ix, x2_clean_ix)

            median_dflux = self.lcs[control_index].get_median_dflux(indices=clean_ix)

            stdev_flux = self.lcs[control_index].get_stdev_flux(indices=clean_ix)
            if stdev_flux is None:
                print(
                    f'WARNING: Could not get flux std dev using clean indices; retrying without preliminary chi-square cut of {cut.params["temp_x2_max_value"]}...'
                )
                stdev_flux = self.lcs[control_index].get_stdev_flux(
                    indices=dflux_clean_ix
                )
                if stdev_flux is None:
                    print(
                        "WARNING: Could not get flux std dev using clean indices; retrying with all indices..."
                    )
                    stdev_flux = self.lcs[control_index].get_stdev_flux(
                        control_index=control_index
                    )

            sigma_extra = get_sigma_extra(median_dflux, stdev_flux)

            stats.loc[control_index, "median_dflux"] = median_dflux
            stats.loc[control_index, "stdev"] = stdev_flux
            stats.loc[control_index, "sigma_extra"] = sigma_extra

        return stats

    def add_noise_to_dflux(self, sigma_extra):
        for control_index in self.get_all_indices():
            self.lcs[control_index].add_noise_to_dflux(sigma_extra)

    def get_all_controls(self):
        controls = [
            deepcopy(self.lcs[control_index].t)
            for control_index in self.lcs
            if control_index > 0
        ]
        all_controls = pdastrostatsclass()
        all_controls.t = pd.concat(controls, ignore_index=True)
        return all_controls

    def calculate_control_stats(self, previous_flags):
        print("Calculating control light curve statistics...")

        len_mjd = len(self.lcs[0].t["MJD"])

        # construct arrays for control lc data
        uJy = np.full((self.num_controls, len_mjd), np.nan)
        duJy = np.full((self.num_controls, len_mjd), np.nan)
        Mask = np.full((self.num_controls, len_mjd), 0, dtype=np.int32)

        i = 1
        for control_index in self.get_control_indices():
            if len(self.lcs[control_index].t) != len_mjd or not np.array_equal(
                self.lcs[0].t["MJD"], self.lcs[control_index].t["MJD"]
            ):
                raise RuntimeError(
                    f"ERROR: SN lc not equal to control lc for control_index {control_index}! Rerun or debug verify_mjds()."
                )
            else:
                uJy[i - 1, :] = self.lcs[control_index].t["uJy"]
                duJy[i - 1, :] = self.lcs[control_index].t[
                    self.lcs[control_index].dflux_colname
                ]
                Mask[i - 1, :] = self.lcs[control_index].t["Mask"]

            i += 1

        c2_param2columnmapping = self.lcs[0].intializecols4statparams(
            prefix="c2_", format4outvals="{:.2f}", skipparams=["converged", "i"]
        )

        for index in range(uJy.shape[-1]):
            pda4MJD = pdastrostatsclass()
            pda4MJD.t["uJy"] = uJy[0:, index]
            pda4MJD.t[self.lcs[0].dflux_colname] = duJy[0:, index]
            pda4MJD.t["Mask"] = np.bitwise_and(Mask[0:, index], previous_flags)

            pda4MJD.calcaverage_sigmacutloop(
                "uJy",
                noisecol=self.lcs[0].dflux_colname,
                maskcol="Mask",
                maskval=previous_flags,
                verbose=1,
                Nsigma=3.0,
                median_firstiteration=True,
            )
            self.lcs[0].statresults2table(
                pda4MJD.statparams, c2_param2columnmapping, destindex=index
            )

    def apply_controls_cut(self, cut: Cut, previous_flags: int):
        self.calculate_control_stats(previous_flags)
        self.lcs[0].t["c2_abs_stn"] = (
            self.lcs[0].t["c2_mean"] / self.lcs[0].t["c2_mean_err"]
        )

        # flag SN measurements
        self.lcs[0].flag_by_control_stats(cut)

        # copy over SN's control cut flags to control light curve 'Mask' columns
        flags_arr = np.full(
            self.lcs[0].t["Mask"].shape,
            (
                cut.flag
                | cut.params["questionable_flag"]
                | cut.params["x2_flag"]
                | cut.params["stn_flag"]
                | cut.params["Nclip_flag"]
                | cut.params["Ngood_flag"]
            ),
        )
        flags_to_copy = np.bitwise_and(self.lcs[0].t["Mask"], flags_arr)
        for control_index in self.get_control_indices():
            self.lcs[control_index].copy_flags(flags_to_copy)

        # self.drop_extra_columns()

        len_ix = len(self.lcs[0].getindices())
        x2_percent_cut = (
            100
            * len(self.lcs[0].ix_masked("Mask", maskval=cut.params["x2_flag"]))
            / len_ix
        )
        stn_percent_cut = (
            100
            * len(self.lcs[0].ix_masked("Mask", maskval=cut.params["stn_flag"]))
            / len_ix
        )
        Nclip_percent_cut = (
            100
            * len(self.lcs[0].ix_masked("Mask", maskval=cut.params["Nclip_flag"]))
            / len_ix
        )
        Ngood_percent_cut = (
            100
            * len(self.lcs[0].ix_masked("Mask", maskval=cut.params["Ngood_flag"]))
            / len_ix
        )
        questionable_percent_cut = (
            100
            * len(
                self.lcs[0].ix_masked("Mask", maskval=cut.params["questionable_flag"])
            )
            / len_ix
        )
        percent_cut = (
            100 * len(self.lcs[0].ix_masked("Mask", maskval=cut.flag)) / len_ix
        )
        return (
            x2_percent_cut,
            stn_percent_cut,
            Nclip_percent_cut,
            Ngood_percent_cut,
            questionable_percent_cut,
            percent_cut,
        )

    def apply_badday_cut(self, cut: Cut, previous_flags, flux2mag_sigmalimit=3.0):
        mjdbinsize = cut.params["mjd_bin_size"]
        avg_sn = AveragedSupernova(
            tnsname=self.tnsname, mjd0=self.mjd0, filt=self.filt, mjdbinsize=mjdbinsize
        )
        avg_sn.num_controls = self.num_controls
        for control_index in self.get_all_indices():
            avg_sn.set_avg_lc(
                self.lcs[control_index].average(
                    cut,
                    previous_flags,
                    mjdbinsize=mjdbinsize,
                    flux2mag_sigmalimit=flux2mag_sigmalimit,
                ),
                control_index=control_index,
            )

        all_flags = (
            previous_flags
            | cut.flag
            | cut.params["ixclip_flag"]
            | cut.params["smallnum_flag"]
        )
        percent_cut = (
            100
            * len(avg_sn.avg_lcs[0].ix_masked("Mask", maskval=all_flags))
            / len(avg_sn.avg_lcs[0].t)
        )
        return avg_sn, percent_cut

    def drop_extra_columns(self):
        for control_index in self.get_all_indices():
            self.lcs[control_index].drop_extra_columns()

    def count_files_in_dir(self, path):
        directory_path = Path(path)
        files = [f for f in directory_path.iterdir() if f.is_file()]
        return len(files)

    def load(self, input_dir, control_index=0, cleaned=False):
        self.lcs[control_index] = LightCurve(
            control_index=control_index, filt=self.filt
        )
        self.lcs[control_index].load_lc(input_dir, self.tnsname, cleaned=cleaned)

    def load_all(self, input_dir, num_controls=0, cleaned=False):
        self.lcs = {}
        self.num_controls = 0

        print(f"\nLoading SN light curve and {num_controls} control light curves...")

        # load SN light curve
        self.load(input_dir, cleaned=cleaned)

        if num_controls > 0:
            # keep iterating over control indices until we successfully load num_controls light curves
            control_index = 1
            while self.num_controls < num_controls:
                try:
                    self.load(input_dir, control_index=control_index, cleaned=cleaned)
                    self.num_controls += 1
                except:
                    print(
                        f"Could not load control light curve {control_index}; skipping..."
                    )
                    del self.lcs[control_index]
                control_index += 1

        print(
            f"Successfully loaded SN light curve and {self.num_controls} control light curves (control indices: {self.get_control_indices()})"
        )

    def get_all_indices(self):
        if not self.all_indices:
            self.all_indices = list(self.lcs.keys())
            self.all_indices.sort()
        return self.all_indices

    def get_control_indices(self):
        if not self.control_indices:
            self.control_indices = list(self.lcs.keys())
            if 0 in self.control_indices:
                self.control_indices.remove(0)
            self.control_indices.sort()
        return self.control_indices

    def save_all(self, output_dir, overwrite=False, cleaned=True):
        print(
            f'\nDropping extra columns and saving {"cleaned " if cleaned else ""}SN light curve and {self.num_controls} {"cleaned " if cleaned else ""}control light curves...'
        )
        for control_index in self.get_all_indices():
            self.lcs[control_index].drop_extra_columns()
            self.lcs[control_index].save_lc(
                output_dir, self.tnsname, overwrite=overwrite, cleaned=cleaned
            )
        print("Success")

    def __str__(self):
        return f"SN {self.tnsname} at {self.coords}: MJD0 = {self.mjd0}, {self.num_controls} control light curves"


class AveragedSupernova(Supernova):
    def __init__(
        self,
        tnsname: str = None,
        ra: str = None,
        dec: str = None,
        mjd0: float | None = None,
        mjdbinsize: float = 1.0,
        filt: str = "o",
    ):
        Supernova.__init__(self, tnsname, ra, dec, mjd0, filt)
        self.mjdbinsize = mjdbinsize

        self.avg_lcs: Dict[int, AveragedLightCurve] = {}

    def set_avg_lc(self, lc, control_index=0):
        self.avg_lcs[control_index] = deepcopy(lc)

    def set_avg_lcs(self, lcs):
        self.avg_lcs = deepcopy(lcs)

    def get_avg(self, control_index: int = 0):
        try:
            return self.avg_lcs[control_index].t
        except:
            raise RuntimeError(
                f"Cannot get averaged control light curve {control_index}. Num controls set to {self.num_controls} and {len(self.avg_lcs)} lcs in dictionary."
            )

    def load(self, input_dir, control_index=0):
        self.avg_lcs[control_index] = AveragedLightCurve(
            control_index=control_index, filt=self.filt, mjdbinsize=self.mjdbinsize
        )
        self.avg_lcs[control_index].load_lc(input_dir, self.tnsname)

    def load_all(self, input_dir, num_controls=0):
        self.avg_lcs = {}
        self.num_controls = 0

        print(
            f"\nLoading averaged SN light curve and {num_controls} averaged control light curves..."
        )

        # load averaged SN light curve
        self.load(input_dir)

        if num_controls > 0:
            # keep iterating over control indices until we successfully load num_controls averaged light curves
            control_index = 1
            while self.num_controls < num_controls:
                try:
                    self.load(input_dir, control_index=control_index)
                    self.num_controls += 1
                except:
                    print(
                        f"Could not load control light curve {control_index}; skipping..."
                    )
                    del self.avg_lcs[control_index]
                control_index += 1

        print(
            f"Successfully loaded averaged SN light curve and {self.num_controls} averaged control light curves"
        )

    def save_all(self, output_dir, overwrite=False):
        print(
            f"\nDropping extra columns and saving averaged SN light curve and {self.num_controls} averaged control light curves..."
        )
        for control_index in self.get_all_indices():
            self.avg_lcs[control_index].drop_extra_columns()
            self.avg_lcs[control_index].save_lc(
                output_dir, self.tnsname, overwrite=overwrite
            )
        print("Success")

    def get_all_indices(self):
        if not self.all_indices:
            self.all_indices = list(self.avg_lcs.keys())
            self.all_indices.sort()
        return self.all_indices

    def get_control_indices(self):
        if not self.control_indices:
            self.control_indices = list(self.avg_lcs.keys())
            if 0 in self.control_indices:
                self.control_indices.remove(0)
            self.control_indices.sort()
        return self.control_indices

    def __str__(self):
        return f"Averaged SN {self.tnsname} at {self.coords}: MJD0 = {self.mjd0}, {self.num_controls} control light curves"


# contains either o-band or c-band measurements only
class LightCurve(pdastrostatsclass):
    def __init__(self, control_index=0, filt="o", **kwargs):
        pdastrostatsclass.__init__(self, **kwargs)
        self.control_index = control_index
        self.filt = filt
        self.dflux_colname = "duJy"

    def set_df(self, t: pd.DataFrame):
        self.t = deepcopy(t)

    def get_preMJD0_indices(self, mjd0: float):
        return self.ix_inrange(colnames="MJD", uplim=mjd0, exclude_uplim=True)

    def get_postMJD0_indices(self, mjd0: float):
        return self.ix_inrange(colnames="MJD", lowlim=mjd0)

    def get_good_indices(self, flag: int):
        return self.ix_unmasked("Mask", maskval=flag)

    def get_bad_indices(self, flag: int):
        return self.ix_masked("Mask", maskval=flag)

    def can_plot(self, ix: List[int], columns: List[str] = None):
        if columns is None:
            columns = ["MJD", "uJy", self.dflux_colname]
        return len(ix) > 0 and not self.t.loc[ix, columns].isna().all().all()

    def remove_invalid_rows(self, verbose=False):
        dflux_zero_ix = self.ix_equal(colnames=["duJy"], val=0)
        flux_nan_ix = self.ix_is_null(colnames=["uJy"])
        if len(AorB(dflux_zero_ix, flux_nan_ix)) > 0:
            if verbose:
                print(
                    f"Deleting {len(dflux_zero_ix) + len(flux_nan_ix)} rows with duJy=0 or uJy=NaN..."
                )
            self.t.drop(AorB(dflux_zero_ix, flux_nan_ix), inplace=True)

    def calculate_fdf_column(self, verbose=False):
        # replace infs with NaNs
        if verbose:
            print("Replacing infs with NaNs...")
        self.t.replace([np.inf, -np.inf], np.nan, inplace=True)

        # calculate flux/dflux
        if verbose:
            print("Calculating flux/dflux...")
        self.t[f"uJy/duJy"] = self.t["uJy"] / self.t[self.dflux_colname]

    def get_median_dflux(self, indices=None):
        if indices is None:
            indices = self.getindices()
        return np.nanmedian(self.t.loc[indices, "duJy"])

    def get_stdev_flux(self, indices=None):
        self.calcaverage_sigmacutloop(
            "uJy", indices=indices, Nsigma=3.0, median_firstiteration=True
        )
        return self.statparams["stdev"]

    def add_noise_to_dflux(self, sigma_extra):
        self.t["duJy_new"] = np.sqrt(self.t["duJy"] * self.t["duJy"] + sigma_extra**2)
        self.dflux_colname = "duJy_new"
        self.calculate_fdf_column()

    def flag_by_control_stats(self, cut: Cut):
        # flag SN measurements according to given bounds
        flag_x2_ix = self.ix_inrange(
            colnames=["c2_X2norm"], lowlim=cut.params["x2_max"], exclude_lowlim=True
        )
        flag_stn_ix = self.ix_inrange(
            colnames=["c2_abs_stn"], lowlim=cut.params["stn_max"], exclude_lowlim=True
        )
        flag_nclip_ix = self.ix_inrange(
            colnames=["c2_Nclip"], lowlim=cut.params["Nclip_max"], exclude_lowlim=True
        )
        flag_ngood_ix = self.ix_inrange(
            colnames=["c2_Ngood"], uplim=cut.params["Ngood_min"], exclude_uplim=True
        )
        self.update_mask_column(cut.params["x2_flag"], flag_x2_ix)
        self.update_mask_column(cut.params["stn_flag"], flag_stn_ix)
        self.update_mask_column(cut.params["Nclip_flag"], flag_nclip_ix)
        self.update_mask_column(cut.params["Ngood_flag"], flag_ngood_ix)

        # update mask column with control light curve cut on any measurements flagged according to given bounds
        zero_Nclip_ix = self.ix_equal("c2_Nclip", 0)
        unmasked_ix = self.ix_unmasked(
            "Mask",
            maskval=cut.params["x2_flag"]
            | cut.params["stn_flag"]
            | cut.params["Nclip_flag"]
            | cut.params["Ngood_flag"],
        )
        self.update_mask_column(
            cut.params["questionable_flag"], AnotB(unmasked_ix, zero_Nclip_ix)
        )
        self.update_mask_column(cut.flag, AnotB(self.getindices(), unmasked_ix))

    def copy_flags(self, flags_to_copy):
        self.t["Mask"] = self.t["Mask"].astype(np.int32)
        if len(self.t) < 1:
            return
        elif len(self.t) == 1:
            self.t.loc[0, "Mask"] = int(self.t.loc[0, "Mask"]) | flags_to_copy
        else:
            self.t["Mask"] = np.bitwise_or(self.t["Mask"], flags_to_copy)

    def average(
        self, cut: Cut, previous_flags, mjdbinsize=1.0, flux2mag_sigmalimit=3.0
    ):
        avg_lc = AveragedLightCurve(
            self.control_index,
            filt=self.filt,
            mjdbinsize=mjdbinsize,
            columns=[
                "MJD",
                "MJDbin",
                "uJy",
                "duJy",
                "stdev",
                "x2",
                "Nclip",
                "Ngood",
                "Nexcluded",
                "Mask",
            ],
            hexcols=["Mask"],
        )
        if self.control_index == 0:
            print(f"Now averaging SN light curve...")
        else:
            print(f"Now averaging control light curve {self.control_index}...")

        mjd = int(np.amin(self.t["MJD"]))
        mjd_max = int(np.amax(self.t["MJD"])) + 1

        while mjd <= mjd_max:
            range_ix = self.ix_inrange(
                colnames=["MJD"], lowlim=mjd, uplim=mjd + mjdbinsize, exclude_uplim=True
            )
            range_good_ix = self.ix_unmasked(
                "Mask", maskval=previous_flags, indices=range_ix
            )

            # add new row to averaged light curve
            new_row = {
                "MJDbin": mjd + 0.5 * mjdbinsize,
                "Nclip": 0,
                "Ngood": 0,
                "Nexcluded": len(range_ix) - len(range_good_ix),
                "Mask": 0,
            }
            avglc_index = avg_lc.newrow(new_row)

            # if no measurements present, flag or skip over day
            if len(range_ix) < 1:
                avg_lc.update_mask_column(cut.flag, [avglc_index], remove_old=False)
                mjd += mjdbinsize
                continue

            # if no good measurements, average values anyway and flag
            if len(range_good_ix) < 1:
                # average flux
                self.calcaverage_sigmacutloop(
                    "uJy",
                    noisecol=self.dflux_colname,
                    indices=range_ix,
                    Nsigma=3.0,
                    median_firstiteration=True,
                )
                fluxstatparams = deepcopy(self.statparams)

                # get average mjd
                self.calcaverage_sigmacutloop(
                    "MJD", indices=range_ix, Nsigma=0, median_firstiteration=False
                )
                avg_mjd = self.statparams["mean"]

                # add row and flag
                row = {
                    "MJD": avg_mjd,
                    "uJy": (
                        fluxstatparams["mean"]
                        if not fluxstatparams["mean"] is None
                        else np.nan
                    ),
                    "duJy": (
                        fluxstatparams["mean_err"]
                        if not fluxstatparams["mean_err"] is None
                        else np.nan
                    ),
                    "stdev": (
                        fluxstatparams["stdev"]
                        if not fluxstatparams["stdev"] is None
                        else np.nan
                    ),
                    "x2": (
                        fluxstatparams["X2norm"]
                        if not fluxstatparams["X2norm"] is None
                        else np.nan
                    ),
                    "Nclip": (
                        fluxstatparams["Nclip"]
                        if not fluxstatparams["Nclip"] is None
                        else np.nan
                    ),
                    "Ngood": (
                        fluxstatparams["Ngood"]
                        if not fluxstatparams["Ngood"] is None
                        else np.nan
                    ),
                    "Mask": 0,
                }
                avg_lc.add2row(avglc_index, row)
                self.update_mask_column(cut.flag, range_ix, remove_old=False)
                avg_lc.update_mask_column(cut.flag, [avglc_index], remove_old=False)

                mjd += mjdbinsize
                continue

            # average good measurements
            self.calcaverage_sigmacutloop(
                "uJy",
                noisecol=self.dflux_colname,
                indices=range_good_ix,
                Nsigma=3.0,
                median_firstiteration=True,
            )
            fluxstatparams = deepcopy(self.statparams)

            if fluxstatparams["mean"] is None or len(fluxstatparams["ix_good"]) < 1:
                self.update_mask_column(cut.flag, range_ix, remove_old=False)
                avg_lc.update_mask_column(cut.flag, [avglc_index], remove_old=False)
                mjd += mjdbinsize
                continue

            # get average mjd
            # TODO: SHOULD NOISECOL HERE BE DUJY OR NONE?
            self.calcaverage_sigmacutloop(
                "MJD",
                noisecol=self.dflux_colname,
                indices=fluxstatparams["ix_good"],
                Nsigma=0,
                median_firstiteration=False,
            )
            avg_mjd = self.statparams["mean"]

            # add row to averaged light curve
            row = {
                "MJD": avg_mjd,
                "uJy": fluxstatparams["mean"],
                "duJy": fluxstatparams["mean_err"],
                "stdev": fluxstatparams["stdev"],
                "x2": fluxstatparams["X2norm"],
                "Nclip": fluxstatparams["Nclip"],
                "Ngood": fluxstatparams["Ngood"],
                "Mask": 0,
            }
            avg_lc.add2row(avglc_index, row)

            # flag clipped measurements in lc
            if len(fluxstatparams["ix_clip"]) > 0:
                self.update_mask_column(
                    cut.params["ixclip_flag"],
                    fluxstatparams["ix_clip"],
                    remove_old=False,
                )

            # if small number within this bin, flag measurements
            if len(range_good_ix) < 3:
                self.update_mask_column(
                    cut.params["smallnum_flag"], range_ix, remove_old=False
                )
                avg_lc.update_mask_column(
                    cut.params["smallnum_flag"], [avglc_index], remove_old=False
                )
            # else check sigmacut bounds and flag
            else:
                is_bad = False
                if fluxstatparams["Ngood"] < cut.params["Ngood_min"]:
                    is_bad = True
                if fluxstatparams["Nclip"] > cut.params["Nclip_max"]:
                    is_bad = True
                if (
                    not (fluxstatparams["X2norm"] is None)
                    and fluxstatparams["X2norm"] > cut.params["x2_max"]
                ):
                    is_bad = True
                if is_bad:
                    self.update_mask_column(cut.flag, range_ix, remove_old=False)
                    avg_lc.update_mask_column(cut.flag, [avglc_index], remove_old=False)

            mjd += mjdbinsize

        avg_lc.flux2mag(
            "uJy", "duJy", "m", "dm", zpt=23.9, upperlim_Nsigma=flux2mag_sigmalimit
        )

        # TODO: not sure if needed
        for col in ["Nclip", "Ngood", "Nexcluded", "Mask"]:
            avg_lc.t[col] = avg_lc.t[col].astype(np.int32)

        return avg_lc

    def apply_cut(self, column_name, flag, min_value=None, max_value=None):
        all_ix = self.getindices()
        if not min_value is None or not max_value is None:
            kept_ix = self.ix_inrange(
                colnames=[column_name], lowlim=min_value, uplim=max_value
            )
        else:
            raise RuntimeError(
                f"ERROR: Cannot apply cut without min value ({min_value}) or max value ({max_value})."
            )
        cut_ix = AnotB(all_ix, kept_ix)

        self.update_mask_column(flag, cut_ix)

        percent_cut = 100 * len(cut_ix) / len(all_ix)
        return percent_cut

    def update_mask_column(self, flag, indices, remove_old=True):
        if remove_old:
            # remove any old flags of the same value
            self.t["Mask"] = np.bitwise_and(self.t["Mask"].astype(int), ~flag)

        if len(indices) > 1:
            flag_arr = np.full(self.t.loc[indices, "Mask"].shape, flag)
            self.t.loc[indices, "Mask"] = np.bitwise_or(
                self.t.loc[indices, "Mask"].astype(int), flag_arr
            )
        elif len(indices) == 1:
            self.t.loc[indices, "Mask"] = int(self.t.loc[indices[0], "Mask"]) | flag

    def drop_extra_columns(self, verbose=False):
        dropcols = []
        for col in [
            "Noffsetlc",
            "uJy/duJy",
            "__tmp_SN",
            "SNR",
            "SNRsum",
            "SNRsumnorm",
            "SNRsim",
            "SNRsimsum",
            "c2_mean",
            "c2_mean_err",
            "c2_stdev",
            "c2_stdev_err",
            "c2_X2norm",
            "c2_Ngood",
            "c2_Nclip",
            "c2_Nmask",
            "c2_Nnan",
            "c2_abs_stn",
        ]:
            if col in self.t.columns:
                dropcols.append(col)
        for col in self.t.columns:
            if re.search("^c\d_", col):
                dropcols.append(col)

        if len(dropcols) > 0:
            if verbose:
                print(
                    f'Dropping extra columns ({f"control light curve {str(self.control_index)}" if self.control_index > 0 else "SN light curve"}): ',
                    dropcols,
                )
            self.t.drop(columns=dropcols, inplace=True)

    def check_column_names(self, required_column_names):
        if self.t is None:
            return

        for column_name in required_column_names:
            if not column_name in self.t.columns:
                raise RuntimeError(f"ERROR: Missing required column: {column_name}")

    def load_lc(self, input_dir, tnsname, cleaned=False):
        filename = get_filename(
            input_dir, tnsname, self.filt, self.control_index, cleaned=cleaned
        )
        self.load_lc_by_filename(filename)

    def load_lc_by_filename(self, filename):
        self.load_spacesep(filename, delim_whitespace=True, hexcols=["Mask"])
        self.check_column_names(required_column_names=REQUIRED_COLUMN_NAMES)

    def save_lc(self, output_dir, tnsname, indices=None, overwrite=False, cleaned=True):
        filename = get_filename(
            output_dir, tnsname, self.filt, self.control_index, cleaned=cleaned
        )
        self.save_lc_by_filename(filename, indices=indices, overwrite=overwrite)

    def save_lc_by_filename(self, filename, indices=None, overwrite=False):
        self.write(
            filename=filename, indices=indices, overwrite=overwrite, hexcols=["Mask"]
        )

    def __str__(self):
        return self.t.to_string()


class AveragedLightCurve(LightCurve):
    def __init__(self, control_index=0, filt="o", mjdbinsize=1.0, **kwargs):
        LightCurve.__init__(self, control_index, filt, **kwargs)
        self.mjdbinsize = mjdbinsize

    def load_lc_by_filename(self, filename):
        self.load_spacesep(filename, delim_whitespace=True, hexcols=["Mask"])
        self.check_column_names(required_column_names=REQUIRED_AVG_COLUMN_NAMES)

    def load_lc(self, input_dir, tnsname):
        filename = get_filename(
            input_dir, tnsname, self.filt, self.control_index, self.mjdbinsize
        )
        self.load_lc_by_filename(filename)

    def save_lc(self, output_dir, tnsname, indices=None, overwrite=False):
        filename = get_filename(
            output_dir, tnsname, self.filt, self.control_index, self.mjdbinsize
        )
        self.save_lc_by_filename(filename, indices=indices, overwrite=overwrite)


# will contain measurements from both filters (o-band and c-band)
class FullLightCurve:
    def __init__(
        self, control_index=0, ra: str = None, dec: str = None, mjd0: float = None
    ):
        self.t = None
        self.mjd0 = mjd0
        self.coords = Coordinates(ra, dec)
        self.control_index = control_index

    def get_tns_data(self, tnsname, api_key, tns_id, bot_name):
        if self.coords.is_empty() or self.mjd0 is None or np.isnan(self.mjd0):
            print("Querying TNS for RA, Dec, and discovery date...")
            json_data = query_tns(tnsname, api_key, tns_id, bot_name)
            if json_data is None:
                print(f"Skipping...")
                return

            if self.coords.is_empty():
                self.coords = Coordinates(
                    json_data["data"]["reply"]["ra"], json_data["data"]["reply"]["dec"]
                )
                print(f"Setting coordinates to TNS coordinates: {self.coords}")

            if self.mjd0 is None or np.isnan(self.mjd0):
                disc_date = json_data["data"]["reply"]["discoverydate"]
                date = list(disc_date.partition(" "))[0]
                time = list(disc_date.partition(" "))[2]
                date_object = Time(date + "T" + time, format="isot", scale="utc")
                self.mjd0 = date_object.mjd - DISC_DATE_BUFFER
                print(
                    f"Setting MJD0 to TNS discovery date minus {DISC_DATE_BUFFER}: {self.mjd0}"
                )

    # download the full light curve from ATLAS
    def download(self, headers, lookbacktime=None, max_mjd=None):
        if lookbacktime:
            min_mjd = float(Time.now().mjd - lookbacktime)
        else:
            min_mjd = 50000.0

        if not max_mjd:
            max_mjd = float(Time.now().mjd)

        print(
            f"Downloading ATLAS light curve at {self.coords} from {min_mjd} MJD to {max_mjd} MJD..."
        )

        if min_mjd > max_mjd:
            raise RuntimeError(
                f"ERROR: max MJD {max_mjd} cannot be than min MJD {min_mjd}."
            )

        while True:
            try:
                result = query_atlas(
                    headers,
                    self.coords.ra.angle.degree,
                    self.coords.dec.angle.degree,
                    min_mjd,
                    max_mjd,
                )
                break
            except Exception as e:
                print("Exception caught: " + str(e))
                print("Trying again in 20 seconds! Waiting...")
                time.sleep(20)
                continue
        self.t = result

    def get_filt_lens(self):
        total_len = len(self.t)
        o_len = len(np.where(self.t["F"] == "o")[0])
        c_len = len(np.where(self.t["F"] == "c")[0])
        return total_len, o_len, c_len

    # divide the light curve by filter and save into separate files
    def save(self, input_dir, tnsname, overwrite=False):
        if self.t is None:
            raise RuntimeError(
                "ERROR: Cannot save light curve that hasn't been downloaded yet."
            )

        lc = LightCurve(control_index=self.control_index)
        lc.set_df(self.t)

        # sort data by mjd
        lc.t = lc.t.sort_values(by=["MJD"], ignore_index=True)

        # remove rows with duJy=0 or uJy=NaN
        dflux_zero_ix = lc.ix_equal(colnames=["duJy"], val=0)
        flux_nan_ix = lc.ix_is_null(colnames=["uJy"])
        if len(AorB(dflux_zero_ix, flux_nan_ix)) > 0:
            print(
                f"Deleting {len(dflux_zero_ix) + len(flux_nan_ix)} rows with duJy=0 or uJy=NaN..."
            )
            lc.t = lc.t.drop(AorB(dflux_zero_ix, flux_nan_ix))

        for filt in ATLAS_FILTERS:
            filename = get_filename(
                input_dir, tnsname, filt=filt, control_index=self.control_index
            )
            indices = lc.ix_equal(colnames=["F"], val=filt)
            print(
                f"Saving downloaded light curve with filter {filt} (length {len(indices)}) at {filename}..."
            )
            lc.save_lc_by_filename(filename, indices=indices, overwrite=overwrite)

    def __str__(self):
        return f"Full light curve at {self.coords}: control ID = {self.control_index}, MJD0 = {self.mjd0}"


"""
ADD SIMULATIONS AND APPLY ROLLING SUM TO AVERAGED LIGHT CURVE 
"""


class Simulation(ABC):
    def __init__(self, model_name=None, **kwargs):
        """
        Initialize the Simulation object.
        """
        self.model_name = model_name
        self.peak_appmag = None

    @abstractmethod
    def get_sim_flux(self, mjds, peak_appmag, **kwargs):
        """
        Compute the simulated flux for the given MJDs and peak apparent magnitude.

        :param mjds: List or array of MJDs.
        :param peak_appmag: Desired peak apparent magnitude of the simulation.

        :return: An array of flux values corresponding to the input MJDs.
        """
        pass

    def __str__(self):
        return f'Simulation with model name "{self.model_name}": peak appmag = {self.peak_appmag:0.2f}'


class SimDetecSupernova(AveragedSupernova):
    def __init__(self, tnsname: str = None, mjdbinsize: float = 1.0, filt: str = "o"):
        AveragedSupernova.__init__(
            self, tnsname=tnsname, mjdbinsize=mjdbinsize, filt=filt
        )
        self.avg_lcs: Dict[int, SimDetecLightCurve] = {}

    def apply_rolling_sums(self, sigma_kern: float, flag=0x800000):
        for control_index in self.get_all_indices():
            self.avg_lcs[control_index].apply_rolling_sum(sigma_kern, flag=flag)

    def remove_rolling_sums(self):
        for control_index in self.get_all_indices():
            self.avg_lcs[control_index].remove_rolling_sum()

    def remove_simulations(self):
        for control_index in self.get_all_indices():
            self.avg_lcs[control_index].remove_simulations()

    def load(self, input_dir, control_index=0):
        self.avg_lcs[control_index] = SimDetecLightCurve(
            control_index=control_index, filt=self.filt, mjdbinsize=self.mjdbinsize
        )
        if control_index == 0:
            filename = f"{input_dir}/{self.tnsname}.{self.filt}.{self.mjdbinsize:0.2f}days.lc.txt"
        else:
            filename = f"{input_dir}/controls/{self.tnsname}_i{control_index:03d}.{self.filt}.{self.mjdbinsize:0.2f}days.lc.txt"
        self.avg_lcs[control_index].load_lc_by_filename(filename)


class SimDetecLightCurve(AveragedLightCurve):
    def __init__(self, control_index=0, filt="o", mjd0=None, mjdbinsize=1.0, **kwargs):
        AveragedLightCurve.__init__(self, control_index, filt, mjdbinsize, **kwargs)
        self.cur_sigma_kern = None
        self.pre_mjd0_ix = self.ix_inrange("MJD", uplim=mjd0)
        self.valid_seasons_ix = None

    # remove rolling sum columns
    def remove_rolling_sum(self):
        self.cur_sigma_kern = None
        dropcols = []
        for col in ["__tmp_SN", "SNR", "SNRsum", "SNRsumnorm"]:
            if col in self.t.columns:
                dropcols.append(col)
        if len(dropcols) > 0:
            self.t.drop(columns=dropcols, inplace=True)

    # remove simulation columns
    def remove_simulations(self):
        dropcols = []
        for col in ["__tmp_SN", "uJysim", "SNRsim", "simLC", "SNRsimsum"]:
            if col in self.t.columns:
                dropcols.append(col)
        if len(dropcols) > 0:
            self.t.drop(columns=dropcols, inplace=True)

    # apply a rolling sum to the light curve and add SNR, SNRsum, and SNRsumnorm columns
    def apply_rolling_sum(self, sigma_kern, indices=None, flag=0x800000, verbose=False):
        if indices is None:
            indices = self.getindices()
        if len(indices) < 1:
            raise RuntimeError(
                "ERROR: not enough measurements to apply simulated gaussian"
            )
        good_ix = AandB(indices, self.ix_unmasked("Mask", flag))

        self.remove_rolling_sum()
        self.cur_sigma_kern = sigma_kern
        self.t.loc[indices, "SNR"] = 0.0
        self.t.loc[good_ix, "SNR"] = (
            self.t.loc[good_ix, "uJy"] / self.t.loc[good_ix, "duJy"]
        )

        new_gaussian_sigma = round(sigma_kern / self.mjdbinsize)
        windowsize = int(6 * new_gaussian_sigma)
        halfwindowsize = int(windowsize * 0.5) + 1
        if verbose:
            print(
                f"Sigma: {sigma_kern:0.2f} days; MJD bin size: {self.mjdbinsize:0.2f} days; sigma: {new_gaussian_sigma:0.2f} bins; window size: {windowsize} bins"
            )

        # calculate the rolling SNR sum
        l = len(self.t.loc[indices])
        dataindices = np.array(range(l) + np.full(l, halfwindowsize))
        temp = pd.Series(np.zeros(l + 2 * halfwindowsize), name="SNR", dtype=np.float64)
        temp[dataindices] = self.t.loc[indices, "SNR"]
        SNRsum = temp.rolling(windowsize, center=True, win_type="gaussian").sum(
            std=new_gaussian_sigma
        )
        self.t.loc[indices, "SNRsum"] = list(SNRsum[dataindices])

        # normalize it
        norm_temp = pd.Series(
            np.zeros(l + 2 * halfwindowsize), name="norm", dtype=np.float64
        )
        norm_temp[np.array(range(l) + np.full(l, halfwindowsize))] = np.ones(l)
        norm_temp_sum = norm_temp.rolling(
            windowsize, center=True, win_type="gaussian"
        ).sum(std=new_gaussian_sigma)
        self.t.loc[indices, "SNRsumnorm"] = list(
            SNRsum.loc[dataindices]
            / norm_temp_sum.loc[dataindices]
            * max(norm_temp_sum.loc[dataindices])
        )

    # add simulated flux to the light curve and add SNRsim and SNRsimsum columns
    def add_sim_flux(
        self,
        lc,
        good_ix,
        sim_flux,
        cur_sigma_kern=None,
        verbose=False,
        remove_old=True,
    ):
        """
        Add simulated flux to the light curve ("uJysim" column) and add "SNRsim" and "SNRsimsum" columns.

        :param lc: Light curve to add the simulated flux to.
        :param good_ix: Unmasked/unflagged indices of the light curve.
        :param cur_sigma_kern: The current kernel size of the rolling sum.
        :param remove_old: Remove any old simulations before adding the simulated flux.
        """
        if cur_sigma_kern is None:
            cur_sigma_kern = self.cur_sigma_kern
        if cur_sigma_kern is None:
            raise RuntimeError(
                "ERROR: No current sigma kern passed as argument or stored during previously applied rolling sum."
            )

        if remove_old:
            lc.remove_simulations()
            lc.t.loc[good_ix, "uJysim"] = lc.t.loc[good_ix, "uJy"]
        lc.t.loc[good_ix, "uJysim"] += sim_flux

        # make sure all bad rows have SNRsim = 0.0 so they have no impact on the rolling SNRsum
        lc.t["SNRsim"] = 0.0
        # include only simulated flux in the SNR
        lc.t.loc[good_ix, "SNRsim"] = (
            lc.t.loc[good_ix, "uJysim"] / lc.t.loc[good_ix, "duJy"]
        )

        new_gaussian_sigma = round(cur_sigma_kern / self.mjdbinsize)
        windowsize = int(6 * new_gaussian_sigma)
        halfwindowsize = int(windowsize * 0.5) + 1
        if verbose:
            print(
                f"Sigma: {cur_sigma_kern:0.2f} days; MJD bin size: {self.mjdbinsize:0.2f} days; new sigma: {new_gaussian_sigma:0.2f} bins; window size: {windowsize} bins"
            )

        # calculate the rolling SNR sum for SNR with simulated flux
        l = len(self.t)
        dataindices = np.array(range(l) + np.full(l, halfwindowsize))
        temp = pd.Series(
            np.zeros(l + 2 * halfwindowsize), name="SNRsim", dtype=np.float64
        )
        temp[dataindices] = lc.t["SNRsim"]
        SNRsimsum = temp.rolling(windowsize, center=True, win_type="gaussian").sum(
            std=new_gaussian_sigma
        )
        lc.t["SNRsimsum"] = list(SNRsimsum.loc[dataindices])

        return lc

    # add any simulation to the light curve, specifying parameters using keyword arguments
    def add_simulation(
        self,
        sim: Simulation,
        peak_appmag: float,
        cur_sigma_kern=None,
        flag=0x800000,
        verbose=False,
        remove_old=True,
        **kwargs,
    ):
        """
        Add any Simulation object to the light curve, specifying parameters using keyword arguments.

        :param sim: The Simulation to add.
        :param peak_appmag: The desired peak apparent magnitude of the Simulation to add.
        :param cur_sigma_kern: The current sigma of the rolling sum.
        :param flag: The flag value by which to filter out any flagged bins.
        :param remove_old: Remove any old simulations before adding the simulated flux.
        """
        if verbose:
            print(f"Adding simulation: {sim}")

        lc = deepcopy(self)
        good_ix = AandB(lc.getindices(), lc.ix_unmasked("Mask", flag))
        sim_flux = sim.get_sim_flux(lc.t.loc[good_ix, "MJD"], peak_appmag, **kwargs)

        return self.add_sim_flux(
            lc,
            good_ix,
            sim_flux,
            cur_sigma_kern=cur_sigma_kern,
            verbose=verbose,
            remove_old=remove_old,
        )

    # get max FOM (for simulated FOM, column='SNRsimsum'; else column='SNRsumnorm')
    # of measurements within the given indices
    def get_max_fom(self, indices=None, column="SNRsimsum"):
        if indices is None:
            indices = self.getindices()
        max_fom_idx = self.t.loc[indices, column].idxmax()

        max_fom_mjd = self.t.loc[max_fom_idx, "MJDbin"]
        max_fom = self.t.loc[max_fom_idx, column]
        return max_fom_mjd, max_fom
