# -*- coding: utf-8 -*-

import os
import re
import numpy as np

from pysiral.errorhandler import FileIOErrorHandler
from pysiral.envisat.sgdr_mds_def import envisat_get_mds_def
from pysiral.envisat.functions import (mdsr_timestamp_to_datetime,
                                       get_envisat_window_delay,
                                       get_envisat_wfm_range)
from pysiral.esa.header import (ESAProductHeader, ESAScienceDataSetDescriptors)
from pysiral.esa.functions import get_structarr_attr
from pysiral.iotools import ReadNC


class EnvisatSGDR(object):

    _DS_NAME = {
        "ra2": "RA2_DATA_SET_FOR_LEVEL_2",
        "wfm18hz": "RA2_AVERAGE_WAVEFORMS"}

    def __init__(self, settings, raise_on_error=False):

        # Error Handling
        self.settings = settings
        self._init_error_handling(raise_on_error)
        self._baseline = None
        self._radar_mode = "lrm"
        self._filename = None
        self.mph = None
        self.sph = None
        self.dsd = None
        self.mds_ra2 = None
        self.mds_wfm18hz = None
        self.mds_18hz = Envisat18HzArrays()
        # XXX: nor sure if needed
        # self.mds_mwr = None
        # self.mds_wfmburst = None

    def parse(self):
        self._validate()
        with open(self._filename, "r") as self._fh:
            self._parse_mph()
            self._parse_sph()
            self._parse_dsd()
        with open(self._filename, "rb") as self._fh:
            self._parse_mds("ra2")
            self._parse_mds("wfm18hz")
            # XXX: mwr: not necessary?, wfmburst: not in the files?
            # self._parse_mds("mwr")
            # self._parse_mds("wfmburst")

    def get_status(self):
        # XXX: Not much functionality here
        return False

    def post_processing(self):
        """
        The SGDR data structure needs to be streamlined, so that it
        is easy to grab the relevant parameters as indiviual arrays
        """
        self._reform_18Hz_data()

    @property
    def filename(self):
        return self._filename

    @filename.setter
    def filename(self, filename):
        """ Save and validate filenames for header and product file """
        # Test if valid file first
        self._error.file_undefined = not os.path.isfile(filename)
        if self._error.file_undefined:
            return
        self._filename = filename

    @property
    def baseline(self):
        return self._baseline

    @property
    def radar_mode(self):
        return self._radar_mode

    def _init_error_handling(self, raise_on_error):
        self._error = FileIOErrorHandler()
        self._error.raise_on_error = raise_on_error
        self._error.file_undefined = True

    def _parse_mph(self):
        self.mph = EnvisatMainProductHeader()
        self._read_header_lines(self.mph)

    def _parse_sph(self):
        self.sph = EnvisatSpecificProductHeader()
        self._read_header_lines(self.sph)

    def _parse_dsd(self):
        """ Reads the Data Set Descriptors dsd's in the SGDR header """
        self.dsd = ESAScienceDataSetDescriptors()
        self.n_dsd_lines = self.dsd.get_num_lines(self.mph.num_dsd)
        for i in np.arange(self.n_dsd_lines+1):
            line = self._fh.readline()
            self.dsd.parse_line(line)

    def _parse_mds(self, mds_target):
        """ Read the data blocks """
        # Just reopened the file in binary mode -
        # > get start byte and number of data set records
        l1b_data_set_name = self._DS_NAME[mds_target].lower()
        data_set_descriptor = self.dsd.get_by_fieldname(l1b_data_set_name)
        startbyte = int(data_set_descriptor["ds_offset"])
        self.n_msd_records = int(data_set_descriptor["num_dsr"])
        # Set the file pointer
        self._fh.seek(startbyte)
        # Get the parser (depending on MDS target)
        self.mds_definition = envisat_get_mds_def(
            self.n_msd_records, mds_target)
        mds_parser = self.mds_definition.get_mds_parser()
        # Parser the binary part of the .DBL file
        mds = mds_parser.parse(self._fh.read(mds_parser.sizeof()))
        setattr(self, "mds_"+mds_target, mds)

    def _read_header_lines(self, header):
        """ Method to read the MPH and SPH headers are identical """
        line_index = 0
        while line_index < 1000:
            line = self._fh.readline()
            header.scan_line(line)
            # Break if the first SPH field is reached
            if header.last_field(line):
                break
            line_index = +1

    def _reform_18Hz_data(self):
        self.mds_18hz.n_records = self.n_msd_records
        # Take the time stamp from the waveform data
        self.mds_18hz.reform_timestamp(self.mds_wfm18hz)
        # Reform the longitude, latitude, altitude informaiton
        self.mds_18hz.reform_position(self.mds_ra2)
        # Retrieve flag data
        self.mds_18hz.reform_flags(self.mds_ra2)
        # Reform the waveform information
        self.mds_18hz.reform_waveform(self.mds_ra2, self.mds_wfm18hz)
        # Reform the land-mask based surface type flags
        self.mds_18hz.reform_surface_type_flags(self.mds_ra2)

        # Reform the geophysical range corrections
        # (instrumental range corrections are applied in _reform_waveform)
        grc_targets = self.settings.geophysical_correction_targets
        self.mds_18hz.reform_geophysical_corrections(self.mds_ra2, grc_targets)

    def _validate(self):
        pass


class EnvisatMainProductHeader(ESAProductHeader):
    """
    Container for the Main Product Header of Envisat SGDR data
    """

    # Datatypes for automatic conversion
    # A field that is in here is converted to an 32bit integer
    _INT_LIST = [
        "PHASE", "CYCLE",  "REL_ORBIT", "ABS_ORBIT", "SAT_BINARY_TIME",
        "CLOCK_STEP", "LEAP_SIGN", "LEAP_ERR", "PRODUCT_ERR", "TOT_SIZE",
        "SPH_SIZE", "NUM_DSD", "DSD_SIZE", "NUM_DATA_SETS"]

    # A field that is in here is converted to an 32bit float
    _FLOAT_LIST = [
        "DELTA_UT1", "X_POSITION", "Y_POSITION",
        "Z_POSITION", "X_VELOCITY", "Y_VELOCITY", "Z_VELOCITY"]

    def __init__(self):
        super(EnvisatMainProductHeader, self).__init__()

    def last_field(self, line):
        return re.search("NUM_DATA_SETS=", line)


class EnvisatSpecificProductHeader(ESAProductHeader):
    """
    Container for the Specific Product Header of Envisat SGDR science data
    """

    # Datatypes for automatic conversion
    # A field that is in here is converted to an 32bit integer
    _INT_LIST = [
        "RA2_L2_PROC_FLAG", "RA2_L1B_PROC_FLAG", "RA2_L1B_HEADER_FLAG",
        "RA2_FLAG_MANOEUVER", "RA2_IF_MASK_SEL", "RA2_IF_MASK_PROC",
        "RA2_USO_SEL", "RA2_USO_PROC", "AVERAGE_GLOBAL_PRESSURE",
        "SOLAR_ACTIVITY_INDEX", "MWR_L2_PROC_FLAG", "MWR_L1B_PROC_FLAG",
        "MWR_L1B_HEADER_FLAG", "MWR_L1B_TELEMETRY_FLAG"]

    # A field that is in here is converted to an 32bit float
    _FLOAT_LIST = [
        "RA2_FIRST_LAT", "RA2_FIRST_LONG", "RA2_LAST_LAT",
        "RA2_LAST_LONG", "RA2_L2_PROCESSING_QUALITY",
        "RA2_L1B_PROCESSING_QUALITY", "RA2_L1B_HEADER_QUALITY",
        "RA2_L2_PROC_THRESH", "RA2_L1B_PROC_THRESH",
        "RA2_L1B_HEADER_THRESH", "RA2_MEASUREMENT_PERCENT",
        "RA2_320_BAND_PERCENT", "RA2_80_BAND_PERCENT", "RA2_20_BAND_PERCENT",
        "RA2_OCEAN_KU_RETRACK_PERCENT", "RA2_OCEAN_S_RETRACK_PERCENT",
        "RA2_ICE1_KU_RETRACK_PERCENT", "RA2_ICE1_S_RETRACK_PERCENT",
        "RA2_ICE2_KU_RETRACK_PERCENT", "RA2_ICE2_S_RETRACK_PERCENT",
        "RA2_SEAICE_KU_RETRACK_PERCENT", "RA2_PEAKINESS_LOW_PERCENT",
        "RA2_PEAKINESS_HIGH_PERCENT", "MWR_BT_OPTIMAL_INTERPOLATION_PERCENT",
        "RA2_TIME_SHIFT_MIDFRAME", "RA2_TIME_INTERVAL", "MWR_FIRST_LAT",
        "MWR_FIRST_LONG", "MWR_LAST_LAT", "MWR_LAST_LONG",
        "MWR_L2_PROC_QUALITY", "MWR_L1B_PROC_QUALITY", "MWR_L1B_HEAD_QUALITY",
        "MWR_L1B_TELEM_QUALITY", "MWR_L2_PROC_THRESH", "MWR_L1B_PROC_THRESH",
        "MWR_L1B_HEAD_THRESH", "MWR_L1B_TELEM_THRESH",
        "RA2_WS_OPTIMAL_INTERPOLATION_PERCENT", "MWR_LANDFLAG_PERCENT",
        "MWR_SEAFLAG_PERCENT"]

    def __init__(self):
        super(EnvisatSpecificProductHeader, self).__init__()

    def last_field(self, line):
        return re.search("MWR_SEAFLAG_PERCENT=", line)


class Envisat18HzArrays(object):
    """ Reforms the grouped SGDR data into continuous arrays """

    def __init__(self):
        self.registered_parameters = []
        self.n_records = 0
        self.n_blocks = 20

    def reform_timestamp(self, mds):
        """ Creates an array of datetime objects for each 18Hz record """
        # XXX: Current no microsecond correction
        timestamp = np.ndarray(shape=(self.n_records), dtype=object)
        mdsr_timestamp = get_structarr_attr(mds, "utc_timestamp")
        for i in range(self.n_records):
            timestamp[i] = mdsr_timestamp_to_datetime(mdsr_timestamp[i])
        self.timestamp = np.repeat(timestamp, self.n_blocks)

    def reform_position(self, mds):
        # 0) Get the relevant data blocks
        time_orbit = get_structarr_attr(mds, "time_orbit")
        range_block = get_structarr_attr(mds, "range_information")
        # 1) get the 1Hz data from the time_orbit group
        longitude = get_structarr_attr(time_orbit, "longitude")
        latitude = get_structarr_attr(time_orbit, "latitude")
        altitude = get_structarr_attr(time_orbit, "altitude")
        # 2) Get the increments
        lon_inc = get_structarr_attr(range_block, "18hz_longitude_differences")
        lat_inc = get_structarr_attr(range_block, "18hz_latitude_differences")
        alt_inc = get_structarr_attr(time_orbit, "18hz_altitude_differences")
        # 3) Expand the 1Hz position arrays
        self.longitude = np.repeat(longitude, self.n_blocks)
        self.latitude = np.repeat(latitude, self.n_blocks)
        self.altitude = np.repeat(altitude, self.n_blocks)
        # 4) Apply the increments
        # XXX: Current version of get_struct_arr returns datatype objects
        #      for listcontainers => manually set dtype
        self._apply_18Hz_increment(self.longitude, lon_inc.astype(np.float32))
        self._apply_18Hz_increment(self.latitude, lat_inc.astype(np.float32))
        self._apply_18Hz_increment(self.altitude, alt_inc.astype(np.float32))

    def reform_waveform(self, mds_ra2, mds_wfm18hz):
        # Relevant field names
        wfm_tag = "average_wfm_if_corr_ku"
        tracker_range_tag = "18hz_tracker_range_no_doppler_ku"
        doppler_tag = "18Hz_ku_range_doppler"
        slope_tag = "18Hz_ku_range_doppler_slope"
        # First get the echo power
        n_range_bins = 128
        n = self.n_records * self.n_blocks
        self.power = np.ndarray(shape=(n, n_range_bins), dtype=np.float32)
        for dsd in range(self.n_records):
            for block in range(self.n_blocks):
                i = dsd*self.n_blocks + block
                self.power[i, :] = mds_wfm18hz[dsd].wfm[block][wfm_tag]
        # Calculate the window delay for each 18hz waveform
        range_info = get_structarr_attr(mds_ra2, "range_information")
        range_corr = get_structarr_attr(mds_ra2, "range_correction")
        tracker_range = get_structarr_attr(
            range_info, tracker_range_tag, flat=True)
        doppler_correction = get_structarr_attr(
            range_corr, doppler_tag, flat=True)
        slope_correction = get_structarr_attr(
            range_corr, slope_tag, flat=True)
        # Compute the window delay (range to first range bin)
        # given in meter (not in seconds)
        # XXX: Add the instrumental range correction for ku?
        self.window_delay_m = get_envisat_window_delay(
            tracker_range, doppler_correction, slope_correction)
        # Compute the range value for each range bin of the 18hz waveform
        # XXX: Might want to set the range bins automatically
        self.range = get_envisat_wfm_range(self.window_delay_m, n_range_bins)

    def reform_surface_type_flags(self, mds):
        flag = get_structarr_attr(mds, "flag")
        surface_type = get_structarr_attr(flag, "altimeter_surface_type")
        radiometer_flag = get_structarr_attr(flag, "radiometer_land_ocean")
        sea_ice_flag = get_structarr_attr(flag, "sea_ice")
        self.surface_type = np.repeat(surface_type, self.n_blocks)
        self.radiometer_flag = np.repeat(radiometer_flag, self.n_blocks)
        self.sea_ice_flag = np.repeat(sea_ice_flag, self.n_blocks)

    def reform_geophysical_corrections(self, mds, grc_targets):
        """
        Automatically extract the corrections and replicate 1Hz => 18 Hz
        grc_targets is defined in config/mission_def.yaml
        -> envisat.settings.geophysical_correction_targets
        """
        self.sgdr_geophysical_correction_list = []
        for key in grc_targets.keys():
            mds_group = get_structarr_attr(mds, key)
            for correction_name in grc_targets[key]:
                correction = get_structarr_attr(mds_group, correction_name)
                setattr(self, correction_name,
                        np.repeat(correction, self.n_blocks))
                self.sgdr_geophysical_correction_list.append(correction_name)

    def reform_flags(self, mds):
        """
        Retrieves a set of paramaters from the Envisat mds, that are needed
        as flags for waveform flagging and surface type filtering.

        Get the following parameters for the waveform is_valid flag:

        1) MCD flags (time_orbit.measurement_confidence_data)

           mcd[0]: packet_length_error
           mcd[1]: obdh_invalid
           mcd[4]: agc_fault
           mcd[5]: rx_delay_fault
           mcd[6]: waveform_fault

        2) average_ku_chirp_band (must be 0: 320Mhz)

        Get the follwing parameter for surface type classification/filtering

            sea_ice_backscatter: backscatter.18hz_sea_ice_sigma_ku

        """
        time_orbit = get_structarr_attr(mds, "time_orbit")
        mcd = get_structarr_attr(time_orbit, "measurement_confidence_data")
        self.flag_packet_length_error = np.repeat(np.array(
            [record.flag[0] for record in mcd], dtype=bool), self.n_blocks)
        self.flag_obdh_invalid = np.repeat(np.array(
            [record.flag[1] for record in mcd], dtype=bool), self.n_blocks)
        self.flag_agc_fault = np.repeat(np.array(
            [record.flag[4] for record in mcd], dtype=bool), self.n_blocks)
        self.flag_rx_delay_fault = np.repeat(np.array(
            [record.flag[5] for record in mcd], dtype=bool), self.n_blocks)
        self.flag_waveform_fault = np.repeat(np.array(
            [record.flag[6] for record in mcd], dtype=bool), self.n_blocks)
        flags = get_structarr_attr(mds, "flag")
        self.ku_chirp_band_id = np.repeat(get_structarr_attr(
            flags, "average_ku_chirp_band"), self.n_blocks)
        # This needed for surface type classifiation
        backscatter = get_structarr_attr(mds, "backscatter")
        self.sea_ice_backscatter = np.array(get_structarr_attr(
            backscatter, "18hz_sea_ice_sigma_ku", flat=True), dtype=np.float32)

    def _apply_18Hz_increment(self, data, inc):
        for i in range(self.n_records):
            i0, i1 = i*self.n_blocks, (i+1)*self.n_blocks
            data[i0:i1] += inc[i, :]
