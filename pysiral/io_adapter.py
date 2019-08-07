# -*- coding: utf-8 -*-
"""
Created on Thu Jul 23 15:10:04 2015

@author: Stefan

Modified by FMI in August 2018:
L1bAdapterCryosat._transfer_classifiers
"""

from pysiral.config import PYSIRAL_VERSION
from pysiral.cryosat2.l1bfile import CryoSatL1B
from pysiral.envisat.sgdrfile import EnvisatSGDR
from pysiral.ers.sgdrfile import ERSSGDR
from pysiral.icesat.glah13 import GLAH13HDF
from pysiral.sentinel3.sral_l1b import Sentinel3SRALL1b

from pysiral.cryosat2.functions import (
    get_tai_datetime_from_timestamp, get_cryosat2_wfm_power,
    get_cryosat2_wfm_range)

from pysiral.classifier import (CS2OCOGParameter, CS2LTPP, CS2PulsePeakiness, EnvisatWaveformParameter)

from pysiral.clocks import UTCTAIConverter
from pysiral.esa.functions import get_structarr_attr
from pysiral.flag import ORCondition
from pysiral.helper import parse_datetime_str
from pysiral.path import filename_from_path
from pysiral.surface_type import ESA_SURFACE_TYPE_DICT
from pysiral.waveform import (get_waveforms_peak_power, TFMRALeadingEdgeWidth,
                              get_sar_sigma0)

from datetime import datetime
from dateutil.relativedelta import relativedelta
from netCDF4 import num2date
import numpy as np


class L1bAdapterCryoSat(object):
    """ Converts a CryoSat2 L1b object into a L1bData object """

    def __init__(self, config):
        self.filename = None
        self._config = config
        self._mission = "cryosat2"

    def construct_l1b(self, l1b, header_only=False):
        self.l1b = l1b                        # pointer to L1bData object
        self._read_cryosat2l1b_header()       # Read CryoSat-2 L1b header
        if not header_only:
            self.read_msd()

    def read_msd(self):

        # Read the content of the .DLB & .HDR files and store content in
        # this class for
        self._read_cryosat2l1b_data()

        # Transfer relevant metdata
        self._transfer_metadata()

        # Extract time orbit data group (lon, lat, alt, time)
        self._transfer_timeorbit()

        # Extract waveform data (power, range, mode, flags)
        self._transfer_waveform_collection()

        # Extract range correction and tidal information
        self._transfer_range_corrections()

        # Get the surface type data from the L1b file
        # (will serve mainly as source for land mask at this stage)
        self._transfer_surface_type_data()

        # Extract waveform parameters that will be used for
        # surface type classification in the L2 processing
        self._transfer_classifiers()

        # now that all data is transfered to the l1bdata object,
        # complete the attributes in the metadata container
        self.l1b.update_l1b_metadata()

    def _read_cryosat2l1b_header(self):
        """
        Read the header and L1b file and stores information
        on open ocean coverage and geographical location in the metadata
        file
        """

        self.cs2l1b = CryoSatL1B()
        self.cs2l1b.filename = self.filename
        self.cs2l1b.parse_header()

        # Populate metadata object with key information to decide if
        # l1b file contains sea ice information

        # open ocean percent from speficic product header in *.DBL
        self.l1b.info.set_attribute(
            "open_ocean_percent", self.cs2l1b.sph.open_ocean_percent*(10.**-2))

        # Geographical coverage of data from start/stop positions in
        # specific product header in *.DBL
        start_lat = self.cs2l1b.sph.start_lat*(10.**-6)
        stop_lat = self.cs2l1b.sph.stop_lat*(10.**-6)
        start_lon = self.cs2l1b.sph.start_long*(10.**-6)
        stop_lon = self.cs2l1b.sph.stop_long*(10.**-6)
        self.l1b.info.set_attribute("lat_min", np.amin([start_lat, stop_lat]))
        self.l1b.info.set_attribute("lat_max", np.amax([start_lat, stop_lat]))
        self.l1b.info.set_attribute("lon_min", np.amin([start_lon, stop_lon]))
        self.l1b.info.set_attribute("lon_max", np.amax([start_lon, stop_lon]))
        self.l1b.update_region_name()
        error_status = self.cs2l1b.get_status()
        if error_status:
            # TODO: Needs ErrorHandler
            raise IOError()

    def _read_cryosat2l1b_data(self):
        """ Read the L1b file and create a CryoSat-2 native L1b object """
        self.cs2l1b.parse_mds()
        error_status = self.cs2l1b.get_status()
        if error_status:
            # TODO: Needs ErrorHandler
            raise IOError()
        self.cs2l1b.post_processing()

    def _transfer_metadata(self):

        info = self.l1b.info

        # Processing System info
        info.set_attribute("pysiral_version", PYSIRAL_VERSION)

        # General CryoSat-2 metadata
        info.set_attribute("mission", self._mission)
        info.set_attribute("mission_data_version", self.cs2l1b.baseline)
        info.set_attribute("orbit", self.cs2l1b.sph.abs_orbit_start)
        info.set_attribute("cycle", self.cs2l1b.mph.cycle)
        mission_data_source = filename_from_path(self.cs2l1b.filename)
        info.set_attribute("mission_data_source", mission_data_source)

        # Get the product timeliness from the processor stage code
        # `proc_stage` in the L1b main product header uses the following
        # conventions:  N=Near-Real Time, T=Test, O=Off Line (Systematic),
        # R=Reprocessing, L=Long Term Archive
        #
        # We do want to use the following timelines codes adopted from the
        # S3 notation: NRT: Near-real time, STC: short-time critical
        # NTC: Non-Time Critical, REP: Reprocessed
        #
        # We therefore define a dictionary and use NTC as default value
        timeliness_dct = {"N": "NRT", "O": "NTC", "R": "REP", "L": "REP"}
        proc_stage = self.cs2l1b.mph.get_by_fieldname("proc_stage")[0]
        info.set_attribute("timeliness", timeliness_dct.get(proc_stage, "NTC"))

        # Time-Orbit Metadata
        start_time = parse_datetime_str(self.cs2l1b.sph.start_record_tai_time)
        stop_time = parse_datetime_str(self.cs2l1b.sph.stop_record_tai_time)
        info.set_attribute("start_time", start_time)
        info.set_attribute("stop_time", stop_time)

    def _transfer_timeorbit(self):

        # Transfer the orbit position
        longitude = get_structarr_attr(self.cs2l1b.time_orbit, "longitude")
        latitude = get_structarr_attr(self.cs2l1b.time_orbit, "latitude")
        altitude = get_structarr_attr(self.cs2l1b.time_orbit, "altitude")
        self.l1b.time_orbit.set_position(longitude, latitude, altitude)

        # Transfer the timestamp
        tai_objects = get_structarr_attr(
            self.cs2l1b.time_orbit, "tai_timestamp")
        tai_timestamp = get_tai_datetime_from_timestamp(tai_objects)

        # Convert the TAI timestamp to UTC
        # XXX: Note, the leap seconds are only corrected based on the
        # first timestamp to avoid having identical timestamps.
        # In the unlikely case this will cause problems in orbits that
        # span over a leap seconds change, set check_all=True
        converter = UTCTAIConverter()
        utc_timestamp = converter.tai2utc(tai_timestamp, check_all=False)
        self.l1b.time_orbit.timestamp = utc_timestamp

        # Set antenna pitch, roll, yaw (dummy values for now)
        dummy_val = np.full(longitude.shape, np.nan)
        self.l1b.time_orbit.set_antenna_attitude(dummy_val, dummy_val, dummy_val)

    def _transfer_waveform_collection(self):

        # Create the numpy arrays for power & range
        dtype = np.float32
        n_records = len(self.cs2l1b.waveform)
        n_range_bins = len(self.cs2l1b.waveform[0].wfm)
        echo_power = np.ndarray(shape=(n_records, n_range_bins), dtype=dtype)
        echo_range = np.ndarray(shape=(n_records, n_range_bins), dtype=dtype)

        # Set the echo power in dB and calculate range
        # XXX: This might need to be switchable
        for i, record in enumerate(self.cs2l1b.waveform):
            echo_power[i, :] = get_cryosat2_wfm_power(
                np.array(record.wfm).astype(np.float32),
                record.linear_scale, record.power_scale)
            echo_range[i, :] = get_cryosat2_wfm_range(
                self.cs2l1b.measurement[i].window_delay, n_range_bins)

        # Transfer the waveform data
        self.l1b.waveform.set_waveform_data(echo_power, echo_range, self.cs2l1b.radar_mode)

        # Transfer waveform flag


    def _transfer_range_corrections(self):
        # Transfer all the correction in the list
        # TODO: This is too complicated. The unification of grc names should be handled in the l1p config files
        for key in self.cs2l1b.corrections[0].keys():
            if key in self._config.CORRECTION_LIST:
                self.l1b.correction.set_parameter(
                    key, get_structarr_attr(self.cs2l1b.corrections, key))
        # CryoSat-2 specific: Two different sources of ionospheric corrections
        options = self._config.get_mission_defaults(self._mission)
        key = options["ionospheric_correction_source"]
        ionospheric = get_structarr_attr(self.cs2l1b.corrections, key)
        self.l1b.correction.set_parameter("ionospheric", ionospheric)

    def _transfer_surface_type_data(self):
        # L1b surface type flag word
        surface_type = get_structarr_attr(
            self.cs2l1b.corrections, "surface_type")
        for key in ESA_SURFACE_TYPE_DICT.keys():
            flag = surface_type == ESA_SURFACE_TYPE_DICT[key]
            self.l1b.surface_type.add_flag(flag, key)

    def _transfer_classifiers(self):

        # Add L1b beam parameter group
        beam_parameter_list = [
            "stack_standard_deviation", "stack_centre",
            "stack_scaled_amplitude", "stack_skewness", "stack_kurtosis"]
        for beam_parameter_name in beam_parameter_list:
            recs = get_structarr_attr(self.cs2l1b.waveform, "beam")
            beam_parameter = [rec[beam_parameter_name] for rec in recs]
            self.l1b.classifier.add(beam_parameter, beam_parameter_name)

        # Calculate Parameters from waveform counts
        # XXX: This is a legacy of the CS2AWI IDL processor
        #      Threshold defined for waveform counts not power in dB
        wfm = get_structarr_attr(self.cs2l1b.waveform, "wfm")

        # Calculate the OCOG Parameter (CryoSat-2 notation)
        ocog = CS2OCOGParameter(wfm)
        self.l1b.classifier.add(ocog.width, "ocog_width")
        self.l1b.classifier.add(ocog.amplitude, "ocog_amplitude")

        # Calculate the Peakiness (CryoSat-2 notation)
        pulse = CS2PulsePeakiness(wfm)
        self.l1b.classifier.add(pulse.peakiness, "peakiness")
        self.l1b.classifier.add(pulse.peakiness_r, "peakiness_r")
        self.l1b.classifier.add(pulse.peakiness_l, "peakiness_l")

        # fmi version: Calculate the LTPP
        ltpp = CS2LTPP(wfm)
        self.l1b.classifier.add(ltpp.ltpp, "late_tail_to_peak_power")

        # Add the peak power (in Watts)
        # (use l1b waveform power array that is already in physical units)
        peak_power = get_waveforms_peak_power(self.l1b.waveform.power, dB=True)
        self.l1b.classifier.add(peak_power, "peak_power_db")

        # Compute the leading edge width (requires TFMRA retracking)
        wfm = self.l1b.waveform.power
        rng = self.l1b.waveform.range
        radar_mode = self.l1b.waveform.radar_mode
        is_ocean = self.l1b.surface_type.get_by_name("ocean").flag
        # fmi version: add of LEW
        width = TFMRALeadingEdgeWidth(rng, wfm, radar_mode, is_ocean)
        lew = width.get_width_from_thresholds(0.05, 0.95)
        lew1 = width.get_width_from_thresholds(0.05, 0.5)
        lew2 = width.get_width_from_thresholds(0.5, 0.95)
        self.l1b.classifier.add(lew, "leading_edge_width")
        self.l1b.classifier.add(lew1, "leading_edge_width_first_half")
        self.l1b.classifier.add(lew2, "leading_edge_width_second_half")
        self.l1b.classifier.add(width.fmi, "first_maximum_index")

        # Compute sigma nought
        peak_power = get_waveforms_peak_power(self.l1b.waveform.power)
        tx_power = get_structarr_attr(self.cs2l1b.measurement, "tx_power")
        altitude = self.l1b.time_orbit.altitude
        v = get_structarr_attr(self.cs2l1b.time_orbit, "satellite_velocity")
        vx2, vy2, vz2 = v[:, 0]**2., v[:, 1]**2., v[:, 2]**2
        vx2, vy2, vz2 = vx2.astype(float), vy2.astype(float), vz2.astype(float)
        velocity = np.sqrt(vx2+vy2+vz2)
        sigma0 = get_sar_sigma0(peak_power, tx_power, altitude, velocity)
        self.l1b.classifier.add(sigma0, "sigma0")


class L1bAdapterEnvisat(object):
    """ Converts a Envisat SGDR object into a L1bData object """

    def __init__(self, config):
        self.filename = None
        self._config = config
        self._mission = "envisat"
        self.settings = config.get_mission_settings(self._mission)

    def construct_l1b(self, l1b):
        """
        Read the Envisat SGDR file and transfers its content to a
        Level1bData instance
        """
        self.l1b = l1b                        # pointer to L1bData object
        # t0 = time.time()
        self._read_envisat_sgdr()             # Read Envisat SGDR data file
        # t1 = time.time()
        # print "Parsing Envisat SGDR file in %.3g seconds" % (t1 - t0)
        self._transfer_metadata()             # (orbit, radar mode, ..)
        self._transfer_timeorbit()            # (lon, lat, alt, time)
        self._transfer_waveform_collection()  # (power, range)
        self._transfer_range_corrections()    # (range corrections)
        self._transfer_surface_type_data()    # (land flag, ocean flag, ...)
        self._transfer_classifiers()          # (beam parameters, flags, ...)

    def _read_envisat_sgdr(self):
        """ Read the L1b file and create a CryoSat-2 native L1b object """
        self.sgdr = EnvisatSGDR(self.settings)
        self.sgdr.filename = self.filename
        self.sgdr.parse()
        error_status = self.sgdr.get_status()
        if error_status:
            # TODO: Needs ErrorHandler
            raise IOError()
        self.sgdr.post_processing()

    def _transfer_metadata(self):
        """ Extract essential metadata information from SGDR file """
        info = self.l1b.info
        sgdr = self.sgdr
        info.set_attribute("pysiral_version", PYSIRAL_VERSION)
        info.set_attribute("mission", self._mission)
        info.set_attribute("mission_data_version", "final v9.3p5")
        info.set_attribute("orbit", sgdr.mph.abs_orbit)
        info.set_attribute("cycle", sgdr.mph.cycle)
        info.set_attribute("cycle", sgdr.mph.cycle)
        info.set_attribute("timeliness", "REP")
        mission_data_source = filename_from_path(sgdr.filename)
        info.set_attribute("mission_data_source", mission_data_source)

    def _transfer_timeorbit(self):
        """ Extracts the time/orbit data group from the SGDR data """
        # Transfer the orbit position
        self.l1b.time_orbit.set_position(
            self.sgdr.mds_18hz.longitude,
            self.sgdr.mds_18hz.latitude,
            self.sgdr.mds_18hz.altitude)
        # Transfer the timestamp
        self.l1b.time_orbit.timestamp = self.sgdr.mds_18hz.timestamp
        # Update meta data container
        self.l1b.update_data_limit_attributes()

    def _transfer_waveform_collection(self):
        """ Transfers the waveform data (power & range for each range bin) """
        from pysiral.flag import ANDCondition
        # Transfer the reformed 18Hz waveforms
        self.l1b.waveform.set_waveform_data(
            self.sgdr.mds_18hz.power,
            self.sgdr.mds_18hz.range,
            self.sgdr.radar_mode)
        # This is from SICCI-1 processor, check of mcd flags
        valid = ANDCondition()
        valid.add(np.logical_not(self.sgdr.mds_18hz.flag_packet_length_error))
        valid.add(np.logical_not(self.sgdr.mds_18hz.flag_obdh_invalid))
        valid.add(np.logical_not(self.sgdr.mds_18hz.flag_agc_fault))
        valid.add(np.logical_not(self.sgdr.mds_18hz.flag_rx_delay_fault))
        valid.add(np.logical_not(self.sgdr.mds_18hz.flag_waveform_fault))
        # ku_chirp_band_id (0 -> 320Mhz)
        valid.add(self.sgdr.mds_18hz.ku_chirp_band_id == 0)
        self.l1b.waveform.set_valid_flag(valid.flag)

    def _transfer_range_corrections(self):

        # Transfer all the correction in the list
        mds = self.sgdr.mds_18hz
        for correction_name in mds.sgdr_geophysical_correction_list:
            self.l1b.correction.set_parameter(
                    correction_name, getattr(mds, correction_name))

    def _transfer_surface_type_data(self):
        surface_type = self.sgdr.mds_18hz.surface_type
        for key in ESA_SURFACE_TYPE_DICT.keys():
            flag = surface_type == ESA_SURFACE_TYPE_DICT[key]
            self.l1b.surface_type.add_flag(flag, key)

    def _transfer_classifiers(self):
        """
        OCOC retracker parameters are needed for surface type classification
        in Envisat L2 processing
        """
        wfm = self.sgdr.mds_18hz.power
        parameter = EnvisatWaveformParameter(wfm)
        self.l1b.classifier.add(parameter.peakiness, "peakiness")
        self.l1b.classifier.add(parameter.peakiness_old, "peakiness_old")

        sigma0 = self.sgdr.mds_18hz.sea_ice_backscatter
        self.l1b.classifier.add(sigma0, "sigma0")

        # Compute the leading edge width (requires TFMRA retracking)
        wfm = self.l1b.waveform.power
        rng = self.l1b.waveform.range
        radar_mode = self.l1b.waveform.radar_mode
        is_ocean = self.l1b.surface_type.get_by_name("ocean").flag
        lew = TFMRALeadingEdgeWidth(rng, wfm, radar_mode, is_ocean)
        lew1 = lew.get_width_from_thresholds(0.05, 0.5)
        lew2 = lew.get_width_from_thresholds(0.5, 0.95)
        self.l1b.classifier.add(lew1, "leading_edge_width_first_half")
        self.l1b.classifier.add(lew2, "leading_edge_width_second_half")
        self.l1b.classifier.add(lew.fmi, "first_maximum_index")


class L1bAdapterERS(object):
    """ Converts a Envisat SGDR object into a L1bData object """

    def __init__(self, config, mission):
        self.filename = None
        self._config = config
        self._mission = mission
        self.settings = config.get_mission_settings(mission)

    def construct_l1b(self, l1b):
        """
        Read the Envisat SGDR file and transfers its content to a
        Level1bData instance
        """
        self.l1b = l1b                        # pointer to L1bData object
        # t0 = time.time()
        self._read_ers_sgdr()                 # Read Envisat SGDR data file
        # t1 = time.time()
        # print "Parsing Envisat SGDR file in %.3g seconds" % (t1 - t0)
        self._transfer_metadata()             # (orbit, radar mode, ..)
        self._transfer_timeorbit()            # (lon, lat, alt, time)
        self._transfer_waveform_collection()  # (power, range)
        self._transfer_range_corrections()    # (range corrections)
        self._transfer_surface_type_data()    # (land flag, ocean flag, ...)
        self._transfer_classifiers()          # (beam parameters, flags, ...)

    def _read_ers_sgdr(self):
        """ Read the L1b file and create a ERS native L1b object """
        self.sgdr = ERSSGDR(self.settings)
        self.sgdr.filename = self.filename
        self.sgdr.parse()
        error_status = self.sgdr.get_status()
        if error_status:
            # TODO: Needs ErrorHandler
            raise IOError()
        self.sgdr.post_processing()

    def _transfer_metadata(self):
        pass
        """ Extract essential metadata information from SGDR file """
        info = self.l1b.info
        sgdr = self.sgdr
        info.set_attribute("pysiral_version", PYSIRAL_VERSION)
        info.set_attribute("mission", self._mission)
        info.set_attribute("mission_data_version", sgdr.nc.software_ver)
        info.set_attribute("orbit", sgdr.nc.abs_orbit)
        info.set_attribute("cycle", sgdr.nc.cycle)
        mission_data_source = filename_from_path(sgdr.nc.filename)
        info.set_attribute("mission_data_source", mission_data_source)
        info.set_attribute("timeliness", "REP")

    def _transfer_timeorbit(self):
        """ Extracts the time/orbit data group from the SGDR data """

        # Transfer the orbit position
        self.l1b.time_orbit.set_position(
            self.sgdr.nc.lon_20hz.flatten(),
            self.sgdr.nc.lat_20hz.flatten(),
            self.sgdr.nc.alt_20hz.flatten())

        # Transfer the timestamp
        sgdr_timestamp = self.sgdr.nc.time_20hz.flatten()
        units = self.settings.sgdr_timestamp_units
        calendar = self.settings.sgdr_timestamp_calendar
        timestamp = num2date(sgdr_timestamp, units, calendar)
        self.l1b.time_orbit.timestamp = timestamp

        # Update meta data container
        self.l1b.update_data_limit_attributes()

    def _transfer_waveform_collection(self):
        """ Transfers the waveform data (power & range for each range bin) """

        # Transfer the reformed 18Hz waveforms
        self.l1b.waveform.set_waveform_data(
            self.sgdr.wfm_power,
            self.sgdr.wfm_range,
            self.sgdr.radar_mode)

        # Set valid flag to exclude calibration data
        # (see section 3.5 of Reaper handbook)
        tracking_state = self.sgdr.nc.alt_state_flag_20hz.flatten()
        valid = ORCondition()
        valid.add(tracking_state == 2)
        valid.add(tracking_state == 3)
        self.l1b.waveform.set_valid_flag(valid.flag)

    def _transfer_range_corrections(self):
        """
        Transfer range correction data from the SGDR netCDF to the
        l1bdata object. The parameter are defined in
        config/mission_def.yaml for ers1/ers2
        -> ersX.settings.sgdr_range_correction_targets

        For a description of the parameter see section 3.10 in the
        REAPER handbook
        """
        grc_dict = self.settings.sgdr_range_correction_targets
        for name in grc_dict.keys():
            target_parameter = grc_dict[name]
            if target_parameter is None:
                continue
            correction = getattr(self.sgdr.nc, target_parameter)
            correction = np.repeat(correction, self.settings.sgdr_n_blocks)
            self.l1b.correction.set_parameter(name, correction)

    def _transfer_classifiers(self):
        """
        Transfer classifier parameter from the SGDR netCDF to the
        l1bdata object. Most parameter are defined in
        config/mission_def.yaml for ers1/ers2
        -> ersX.settings.sgdr_range_correction_targets
        """
        target_dict = self.settings.sgdr_classifier_targets
        for parameter_name in target_dict.keys():
            nc_parameter_name = target_dict[parameter_name]
            nc_parameter = getattr(self.sgdr.nc, nc_parameter_name)
            self.l1b.classifier.add(nc_parameter.flatten(), parameter_name)

        # Add consistent definition of pulse peakiness
        parameter = EnvisatWaveformParameter(self.l1b.waveform.power)
        self.l1b.classifier.add(parameter.pulse_peakiness, "pulse_peakiness")

    def _transfer_surface_type_data(self):
        surface_type = self.sgdr.nc.surface_type
        surface_type = np.repeat(surface_type, self.settings.sgdr_n_blocks)
        for key in ESA_SURFACE_TYPE_DICT.keys():
            flag = surface_type == ESA_SURFACE_TYPE_DICT[key]
            self.l1b.surface_type.add_flag(flag, key)


class L1bAdapterERS1(L1bAdapterERS):
    """ Class for ERS-1 """
    def __init__(self, config):
        super(L1bAdapterERS1, self).__init__(config, "ers1")


class L1bAdapterERS2(L1bAdapterERS):
    """ Class for ERS-1 """
    def __init__(self, config):
        super(L1bAdapterERS2, self).__init__(config, "ers2")


class L1bAdapterSentinel3(object):
    """ Class for Sentinel3X """

    def __init__(self, config, mission):
        self.filename = None
        self._mission = mission
        self._config = config
        self.settings = config.get_mission_settings(mission)
        self.error_status = False

    def construct_l1b(self, l1b, header_only=False):
        """
        Read the Envisat SGDR file and transfers its content to a
        Level1bData instance
        """
        self.l1b = l1b
        self._read_sentinel3_sral_l1b()
        self._transfer_metadata()
        self._test_ku_data_present()

        if not header_only and not self.l1b.error_status:
            self._transfer_timeorbit()
            self._transfer_waveform_collection()
            self._transfer_range_corrections()
            self._transfer_surface_type_data()
            self._transfer_classifiers()
            self.l1b.update_l1b_metadata()

    def _read_sentinel3_sral_l1b(self):
        """ Read the L1b file and create a ERS native L1b object """
        self.sral = Sentinel3SRALL1b()
        self.sral.filename = self.filename
        self.sral.parse_xml_header(self.settings)
        self.sral.parse()
        self.error_status = self.sral.get_status()
        if not self.error_status:
            # TODO: Needs ErrorHandler
            self.sral.post_processing()

    def _transfer_metadata(self):
        """ Extract essential metadata information from SRAL L1 nc file """
        info = self.l1b.info
        product = self.sral.product_info
        info.set_attribute("mission", self._mission)
        info.set_attribute("pysiral_version", PYSIRAL_VERSION)
        info.set_attribute("mission_data_source", self.sral.nc.product_name)
        info.set_attribute("sar_mode_percent", product.sar_mode_percentage)
        info.set_attribute("open_ocean_percent", product.open_ocean_percentage)
        info.set_attribute("orbit", self.sral.nc.absolute_pass_number)
        info.set_attribute("cycle", self.sral.nc.cycle_number)
        info.set_attribute("mission_data_version", self.sral.nc.source)
        info.set_attribute("timeliness", product.timeliness)
        info.set_attribute("lon_min", product.lon_min)
        info.set_attribute("lon_max", product.lon_max)
        info.set_attribute("lat_min", product.lat_min)
        info.set_attribute("lat_max", product.lat_max)

    def _test_ku_data_present(self):
        if not hasattr(self.sral.nc, "time_20_ku"):
            self.error_status = True
            self.l1b.error_status = True

    def _transfer_timeorbit(self):
        """ Extracts the time/orbit data group from the SGDR data """
        from netCDF4 import num2date

        # Transfer the orbit position
        self.l1b.time_orbit.set_position(
            self.sral.nc.lon_20_ku,
            self.sral.nc.lat_20_ku,
            self.sral.nc.alt_20_ku)

        # Transfer the timestamp
        units = self.settings.time_units
        calendar = self.settings.time_calendar
        seconds = self.sral.nc.time_20_ku
        timestamp = num2date(seconds, units, calendar)
        self.l1b.time_orbit.timestamp = timestamp

    def _transfer_waveform_collection(self):
        """ Transfers the waveform data (power & range for each range bin) """

        # self.wfm_power = self.nc.waveform_20_ku
        wfm_power = self.sral.nc.waveform_20_ku
        n_records, n_range_bins = shape = wfm_power.shape

        # Convert wfm counts to power

        # Code from Amandine :
        # waveforms_scaling_dB(k,:) = 10.*log10(waveforms(k,:) * \
        #                             10.^(scale_factor_20_ku'./10));

        # 1. set counts with 0 values to very minor value
        wfm_power[np.where(wfm_power == 0)] = 1e-12

        # 2. get scaling factor
        scale_factor = self.sral.nc.scale_factor_20_ku

        # 3. apply count to power scaling
        # Formular to compute sigma0 (from Jason ALT_PHY_BAC_01 algorithm)
        # sigma0 = K_cal * 10. * log10(power)
        for k in np.arange(n_range_bins):
            wfm_power[:, k] = wfm_power[:, k] * 10.**(scale_factor/10.)
#
#        import matplotlib.pyplot as plt
#        plt.figure()
#        plt.imshow(np.transpose(np.log(wfm_power)), aspect=100)
#        plt.show()
#        plt.colorbar()
#        stop

        # Get the window delay
        # "The tracker_range_20hz is the range measured by the onboard tracker
        #  as the window delay, corrected for instrumental effects and
        #  CoG offset"
        tracker_range = self.sral.nc.tracker_range_20_ku
        net_instr_corrections = self.sral.nc.net_instr_cor_range_20_ku
        tracker_range += net_instr_corrections

        # Compute the range for each range bin
        wfm_range = np.ndarray(shape=shape, dtype=np.float32)
        rbw = self.settings.range_bin_width
        ntb = self.settings.nominal_tracking_bin
        rbi = np.arange(n_range_bins)

        # Loop over each waveform
        for i in np.arange(n_records):
            wfm_range[i, :] = tracker_range[i] + (rbi*rbw) - (ntb*rbw)

        # Transfer to l1b object
        radar_mode = self.settings.radar_mode
        self.l1b.waveform.set_waveform_data(wfm_power, wfm_range, radar_mode)

    def _transfer_range_corrections(self):
        """ Retrieve the geophysical range corrections """

        # The definition of which parameter to choose is set in
        # config/mission_def.yaml
        # (see sentinel3x.options.input_dataset.range_correction_target_dict)
        grc_target_dict = self.settings.range_correction_targets_1Hz
        dummy_val = np.zeros(shape=(self.l1b.n_records), dtype=np.float32)
        time_1Hz = self.sral.nc.time_01
        time_20Hz = self.sral.nc.time_20_ku
        for name in grc_target_dict.keys():
            target_parameter = grc_target_dict[name]
            if target_parameter is not None:
                correction_1Hz = getattr(self.sral.nc, target_parameter)
                correction = np.interp(time_20Hz, time_1Hz, correction_1Hz)
            else:
                correction = dummy_val
            self.l1b.correction.set_parameter(name, correction)

    def _transfer_classifiers(self):
        """
        Transfer the classifiers from L2, L1 (if available) and from
        additional waveform shape analysis
        XXX: This is a makeshift implementation for the expert user
             assessment of early access data
        """

        # Try to get l1 stack parameters (might not be present)
        l1_classifier_target_dict = self.settings.classifier_targets
        for name in l1_classifier_target_dict.keys():
            target_parameter = l1_classifier_target_dict[name]
            classifier = getattr(self.sral.nc, target_parameter)
            self.l1b.classifier.add(classifier, name)

        # Compute sar specific waveform classifiers after Ricker et al. 2014
        wfm = self.l1b.waveform.power

        # Get sigma0 (as peak power)
        sigma0 = get_waveforms_peak_power(wfm, dB=True)
        self.l1b.classifier.add(sigma0, "sigma0")

#        import time
#
#        tick = time.clock()
#        # Calculate the Peakiness (CryoSat-2 notation)
#        pulse = CS2PulsePeakiness(wfm)
#        self.l1b.classifier.add(pulse.peakiness, "peakiness")
#        self.l1b.classifier.add(pulse.peakiness_r, "peakiness_r")
#        self.l1b.classifier.add(pulse.peakiness_l, "peakiness_l")
#        tock = time.clock()
#        print "CS2PulsePeakiness completed in %.1f seconds" % (tock-tick)
#
#        tick = time.clock()
#        # Compute the leading edge width (requires TFMRA retracking)
#        wfm = self.l1b.waveform.power
#        rng = self.l1b.waveform.range
#        radar_mode = self.l1b.waveform.radar_mode
#        is_ocean = self.l1b.surface_type.get_by_name("ocean").flag
#        lew = TFMRALeadingEdgeWidth(rng, wfm, radar_mode, is_ocean)
#        lew1 = lew.get_width_from_thresholds(0.05, 0.5)
#        lew2 = lew.get_width_from_thresholds(0.5, 0.95)
#        self.l1b.classifier.add(lew1, "leading_edge_width_first_half")
#        self.l1b.classifier.add(lew2, "leading_edge_width_second_half")
#        self.l1b.classifier.add(lew.fmi, "first_maximum_index")
#        tock = time.clock()
#        print "TFMRALeadingEdgeWidth completed in %.1f seconds" % (tock-tick)

    def _transfer_surface_type_data(self):
        surface_type = self.sral.nc.surf_type_20_ku
        for key in ESA_SURFACE_TYPE_DICT.keys():
            flag = surface_type == ESA_SURFACE_TYPE_DICT[key]
            self.l1b.surface_type.add_flag(flag, key)


class L1bAdapterSentinel3A(L1bAdapterSentinel3):
    """ Class for Sentinel-3A """

    def __init__(self, config):
        super(L1bAdapterSentinel3A, self).__init__(config, "sentinel3a")


class L1bAdapterICESat(object):
    """ Class for GLAS/ICESat L2 Sea Ice Altimetry Data (HDF5) """

    def __init__(self, config):
        self.filename = None
        self._mission = "icesat"
        self._config = config
        self.settings = config.get_mission_settings("icesat")
        self.error_status = False

    def construct_l1b(self, l1b, header_only=False):
        """
        Read the Envisat SGDR file and transfers its content to a
        Level1bData instance
        """
        self.l1b = l1b
        self._read_icesat_glah13()
        self._transfer_metadata()
        self._compute_equal_distance_indices()

        if not header_only and not self.l1b.error_status:
            self._transfer_timeorbit()
            self._transfer_waveform_collection()
            self._transfer_range_corrections()
            self._transfer_surface_type_data()
            self._transfer_classifiers()
            self.l1b.update_l1b_metadata()

    def _read_icesat_glah13(self):
        """ Parse the GLAH13 file (global attributes, all parameters in
        all data groups)."""
        self.glah13 = GLAH13HDF(self.filename)

    def _transfer_metadata(self):

        info = self.l1b.info
        glah13 = self.glah13

        # Processing System info
        info.set_attribute("pysiral_version", PYSIRAL_VERSION)

        # General CryoSat-2 metadata
        info.set_attribute("mission", self._mission)
        info.set_attribute("mission_data_version", glah13.product_version)
        info.set_attribute("orbit", glah13.get_attr("OrbitNumber"))
        info.set_attribute("is_merged_orbit", True)
        info.set_attribute("cycle", glah13.get_attr("Cycle"))
        info.set_attribute("mission_data_source",
                           glah13.get_attr("LocalGranuleID"))
        info.set_attribute("timeliness", "REP")

        # Time-Orbit Metadata
        start_time = parse_datetime_str(glah13.get_attr("time_coverage_start"))
        stop_time = parse_datetime_str(glah13.get_attr("time_coverage_end"))
        info.set_attribute("start_time", start_time)
        info.set_attribute("stop_time", stop_time)

    def _compute_equal_distance_indices(self):
        """ One main assumption in pysiral is that the input data
        has a more or less equidistant spacing along the orbit. The GLAH13
        data only consists of data for valid freeboards, therefore we
        need to fill the gaps with nodata values """

        # Get 40 Hz time and position
        time_40Hz = self.glah13.get_parameter("Time", "d_UTCTime_40")
        lons_40Hz = self.glah13.get_parameter("Geolocation", "d_lon")
        lons_40Hz[np.where(lons_40Hz > 360.0)] = np.nan
        lats_40Hz = self.glah13.get_parameter("Geolocation", "d_lat")
        lats_40Hz[np.where(np.abs(lats_40Hz) > 90.0)] = np.nan

        # Get 1 Hz time and track indices
        time_1Hz = self.glah13.timestamp_1Hz
        track_id_1Hz = np.array(self.glah13.track_id_1Hz, dtype=int)

        # Interpolate track id to 40Hz data
        track_id_40Hz = np.interp(time_40Hz, time_1Hz, track_id_1Hz)
        track_id_40Hz = np.round(track_id_40Hz).astype(int)

        # Estimate segments in the data that could be continous
        # tracks over sea ice
        segments_start = np.array([0])
        segments_start_indices = np.where(np.ediff1d(time_40Hz) > 300)[0]+1
        segments_start = np.append(segments_start, segments_start_indices)

        segments_stop = segments_start[1:]-1
        segments_stop = np.append(segments_stop, len(time_40Hz)-1)

        # Iteratively estimate the index map for segment wise interpolated
        # 40 Hz output (only interpolated for orbital passes)
        full_40Hz_segments_n_records = 0
        full_40Hz_segments_index = np.array([], dtype=np.int32)
        full_40Hz_segments_index_map = np.array([], dtype=np.int32)
        segment_index_id = 0
        segment_offset = 0
        for start_id, stop_id in zip(segments_start, segments_stop):

            # The data indices of the original data for this segment
            segment_indices = np.arange(start_id, stop_id+1)

            # Convert time to a 40 Hz counter
            time_40hz_segment_raw = time_40Hz[segment_indices]
            segment_counter_40Hz = 40 * (time_40hz_segment_raw -
                                         time_40hz_segment_raw[0])
            segment_counter_40Hz = np.round(segment_counter_40Hz)
            segment_counter_40Hz = segment_counter_40Hz.astype(np.int32)

            # The maximum counter indicates the number of records for
            # the full 40Hz segment
            full_40Hz_segment_records = segment_counter_40Hz[-1] + 1
            full_40Hz_segments_n_records += full_40Hz_segment_records

            # Save the indices maps
            full_40Hz_segments_index = np.append(
                    full_40Hz_segments_index,
                    np.full(full_40Hz_segment_records, segment_index_id))
            segment_index_map = segment_counter_40Hz + segment_offset
            full_40Hz_segments_index_map = np.append(
                    full_40Hz_segments_index_map,
                    segment_index_map)

            # Increase segment index id
            segment_index_id += 1

            # Compute the full index offset for next iteration
            segment_offset += full_40Hz_segment_records

        # Save results to class
        self.track_id_40Hz = track_id_40Hz
        self.full_40Hz_segments_n_records = full_40Hz_segments_n_records
        self.full_40Hz_segments_index = full_40Hz_segments_index
        self.full_40Hz_segments_index_map = full_40Hz_segments_index_map

    def _get_40Hz_from_1Hz(self, variable_1Hz, full=False):
        if full:
            time_ref = self._get_40Hz_full_variable(
                    self.time_40Hz, interpolate=True)
        else:
            time_ref = self.time_40Hz

        variable_40Hz = np.interp(time_ref, self.time_1Hz, variable_1Hz)
        return variable_40Hz

    def _get_40Hz_full_variable(self, variable, interpolate=False,
                                interpolation_variable=None, dtype=None,
                                default=None):
        # Set output type
        if dtype is None:
            dtype = np.float64

        if default is None:
            default = np.nan

        # Map the current variable via an index map on the gap-filled
        # 40 Hz data. Gaps will remain nan
        indices = self.full_40Hz_segments_index_map
        full_variable = np.full(self.full_40Hz_shape, default, dtype=dtype)
        full_variable[indices] = variable

        # Fill Gaps with linear interpolation
        if interpolate:
            if interpolation_variable is None:
                target_x = np.arange(self.full_40Hz_segments_n_records)
                source_x = indices
            else:
                target_x = interpolation_variable
                source_x = interpolation_variable[indices]
            valid = np.where(np.isfinite(variable))[0]
            full_variable = np.interp(target_x, source_x[valid],
                                      variable[valid]).astype(dtype)

        return full_variable

    def _transfer_timeorbit(self):
        """ Extracts the time/orbit data group from the GLAH13 data """

        # Get 40 Hz time and position
        time_40Hz = self.glah13.get_parameter("Time", "d_UTCTime_40")
        lons_40Hz = self.glah13.get_parameter("Geolocation", "d_lon")
        lats_40Hz = self.glah13.get_parameter("Geolocation", "d_lat")

        # Interpolate variables
        time = self._get_40Hz_full_variable(time_40Hz, interpolate=True)

        # Use interpolated time to assist lat/lon interpolation
        # Note: Linear interpolation is an approximation only, but
        #       we do not loose much, since position gaps do not have
        #       valid ranges
        lon = self._get_40Hz_full_variable(lons_40Hz, interpolate=True,
                                           interpolation_variable=time)
        lat = self._get_40Hz_full_variable(lats_40Hz, interpolate=True,
                                           interpolation_variable=time)

        # pysiral l1bdata requires altitude, however this is not available
        # in the glah13 files. Therefore we have to create an artificial one
        # that gives us an easy way to compute the sea ice surface
        # elevation in the Level-2 processor. For simplicity we assume
        # a constant orbit altitude of 590 km (average between perigee and
        # apogee) and later compute a matching range for the predefined
        # sea ice surface elevation
        alt = np.full(time.shape, 590000.)

        # Transfer the orbit position
        self.l1b.time_orbit.set_position(lon, lat, alt)

        # Transfer the timestamp (needs to be datetime)
        # time in glah13 is: seconds since 2000-01-01 12:00:00 UTC
        timestamp = np.ndarray(time.shape, dtype=object)
        datum = datetime(2000, 1, 1, 12, 0, 0)
        for i in np.arange(len(timestamp)):
            timestamp[i] = datum + relativedelta(seconds=time[i])

        self.l1b.time_orbit.timestamp = timestamp

        # Update meta data container
        self.l1b.update_data_limit_attributes()

    def _transfer_waveform_collection(self):
        """ Transfer dummy values (no waveform data in glah13) """

        # Waveform Dummy Data
        dtype = np.float32
        n_records = self.full_40Hz_segments_n_records
        n_range_bins = 3
        radar_mode = "lrm"
        echo_power = np.full((n_records, n_range_bins), 1.0, dtype=dtype)
        echo_range = np.full((n_records, n_range_bins), 0.0, dtype=dtype)

        # Compute range that produces the sea ice surface elevation in
        # the glah13 files with the artificial altitude

        # Get the elevation
        si_elev_40hz = self.glah13.get_parameter("Elevation_Surfaces",
                                                 "d_elev")
        sea_ice_surface_elev = self._get_40Hz_full_variable(si_elev_40hz)

        # Get and apply elecation saturation correction
        elev_sat_corr_40Hz = self.glah13.get_parameter(
                "Elevation_Corrections", "d_satElevCorr")
        elev_sat_corr = self._get_40Hz_full_variable(elev_sat_corr_40Hz)
        sea_ice_surface_elev += elev_sat_corr
        self.sea_ice_surface_elevation_corrected = sea_ice_surface_elev

        # Compute range with altitude from time_orbit group
        altitude = self.l1b.time_orbit.altitude
        laser_range = altitude - sea_ice_surface_elev
        for i in range(3):
            echo_range[:, i] = laser_range + float(i)*10.

        # Set Waveform data
        self.l1b.waveform.set_waveform_data(echo_power, echo_range, radar_mode)

        # Transfer valid flag
        valid_glah13 = self.glah13.get_parameter("Quality", "elev_use_flg")
        valid_glah13 = valid_glah13.astype(bool)
        valid_glah13 = np.logical_not(valid_glah13)
        valid = self._get_40Hz_full_variable(valid_glah13, dtype=bool,
                                             default=False)
        self.l1b.waveform.set_valid_flag(valid)

    def _transfer_range_corrections(self):
        """ Transfer glah13 range correction, though might not be used """

        # Transfer parameters from Geophysical Group
        default = 0.0
        keys = ["d_eqEl", "d_erElv", "d_ldElv", "d_ocElv", "d_poTide"]
        targets = ["equlibrium_tide", "solid_earth_tide", "load_tide",
                   "ocean_tide", "pole_tide"]
        for key, target in zip(keys, targets):
            value_glah13 = self.glah13.get_parameter("Geophysical", key)
            value = self._get_40Hz_full_variable(value_glah13, default=default)
            self.l1b.correction.set_parameter(target, value)

        # Transfer parameters from Elevation_Corrections group
        group_name = "Elevation_Corrections"
        keys = ["d_dTrop", "d_wTrop", "d_satElevCorr", "d_ElevBiasCorr"]
        targets = ["dry_troposphere", "wet_troposphere",
                   "elevation_saturation", "elevation_bias"]
        for key, target in zip(keys, targets):
            value_glah13 = self.glah13.get_parameter(group_name, key)
            value = self._get_40Hz_full_variable(value_glah13, default=default)
            self.l1b.correction.set_parameter(target, value)

    def _transfer_surface_type_data(self):
        """ We need to oversample the 1Hz surface type data """

        # Map the pysiral-recognized surface type codes to 1Hz flag
        # parameters
        # Notes:
        # 1) We map here also the sea ice flag to ocean. This is
        #    because the Level-2 Processor decides what sea ice is and
        #    only looks for ocean "waveforms"
        # 2) We put land as the last one to avoid land elevation
        #    spilling into ocean areas due to ocean flag interpolation
        #    errors
        flag_targets = [("ocean", "is_ocean_1Hz"),
                        ("ocean", "is_seaice_1Hz"),
                        ("land_ice", "is_icesheet_1Hz"),
                        ("land", "is_land_1Hz")]

        # Retrieve the 1Hz data, interpolate to full 40Hz and set the
        # pysiral compliant surface type flag
        for surface_code, glah13_parameter_name in flag_targets:
            flag_1Hz = getattr(self.glah13, glah13_parameter_name)
            flag_1Hz = flag_1Hz.astype(bool)
            flag_40Hz = self._get_40Hz_from_1Hz(flag_1Hz)
            flag_40Hz = flag_40Hz.astype(int)
            flag_full_40Hz = self._get_40Hz_full_variable(
                    flag_40Hz, dtype=bool, default=False)
            self.l1b.surface_type.add_flag(flag_full_40Hz, surface_code)

#        x = np.arange(self.full_40Hz_segments_n_records)
#        import matplotlib.pyplot as plt
#        plt.figure()
#        plt.scatter(x, self.l1b.surface_type.flag)
#        plt.show()
#        stop

    def _transfer_classifiers(self):
        """ Transfer parameter that can be used for surface type
        classification """

        # Transfer parameters from Waveform group
        group_name = "Waveform"
        keys = ["d_kurt2", "d_maxRecAmp", "d_maxSmAmp", "d_skew2",
                "i_gval_rcv", "i_numPk"]
        targets = ["echo_kurtosis", "echo_peak_power",
                   "smoothed_echo_peak_power", "echo_skewness",
                   "echo_gain", "echo_n_peaks"]
        for key, target in zip(keys, targets):
            value_glah13 = self.glah13.get_parameter(group_name, key)
            value = self._get_40Hz_full_variable(value_glah13)
            self.l1b.classifier.add(value, target)

        # Transfer parameters from the Reflectivity group
        group_name = "Reflectivity"
        keys = ["d_RecNrgAll", "d_reflctUC", "d_satNrgCorr", "d_sDevNsOb1"]
        targets = ["received_energy", "reflectivity",
                   "energy_saturation_correction", "background_noise_sdev"]
        for key, target in zip(keys, targets):
            value_glah13 = self.glah13.get_parameter(group_name, key)
            value = self._get_40Hz_full_variable(value_glah13)
            self.l1b.classifier.add(value, target)

        # Add Gaussian fit standard deviation
        group_name = "Elevation_Surfaces"
        keys = ["d_SeaIceVar"]
        targets = ["gaussian_variance"]
        for key, target in zip(keys, targets):
            value_glah13 = self.glah13.get_parameter(group_name, key)
            value = self._get_40Hz_full_variable(value_glah13)
            self.l1b.classifier.add(value, target)

        # Add corrected sea ice surface elevation
        value = self.sea_ice_surface_elevation_corrected

        # Remove elevations over land
        is_land = self.l1b.surface_type.land.indices
        value[is_land] = np.nan

        # Remove invalid elevations
        is_valid = self.l1b.waveform.is_valid
        value[np.where(np.logical_not(is_valid))[0]] = np.nan

        # Last sanity check
        invalid = np.where(value > 100.)[0]
        value[invalid] = np.nan

        self.l1b.classifier.add(value, "sea_ice_surface_elevation_corrected")

        # Add reflectivity correction
        reflect_corr_1Hz = self.glah13.reflect_corr_1Hz
        reflect_corr_40Hz_full = self._get_40Hz_from_1Hz(
                reflect_corr_1Hz, full=True)
        self.l1b.classifier.add(reflect_corr_40Hz_full,
                                "reflectivity_correction")

        # Transfer parameters from the Elevation_Flags group
        group_name = "Elevation_Flags"
        keys = ["elv_cloud_flg"]
        targets = ["cloud_flag"]
        for key, target in zip(keys, targets):
            value_glah13 = self.glah13.get_parameter(group_name, key)
            value = self._get_40Hz_full_variable(value_glah13)
            self.l1b.classifier.add(value, target)

        # Add track & orbit segment id (for preprocessor)
        self.l1b.classifier.add(self.full_40Hz_segments_index,
                                "orbit_segment_id")

        track_id = self._get_40Hz_full_variable(
                self.track_id_40Hz, interpolate=True, dtype=np.int16)
        self.l1b.classifier.add(track_id, "track_id")

    @property
    def segment_ids(self):
        return np.unique(self.full_40Hz_segments_index)

    @property
    def full_40Hz_shape(self):
        return (self.full_40Hz_segments_n_records)

    @property
    def time_1Hz(self):
        return self.glah13.timestamp_1Hz

    @property
    def time_40Hz(self):
        return self.glah13.get_parameter("Time", "d_UTCTime_40")
