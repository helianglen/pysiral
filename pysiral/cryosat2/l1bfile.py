# -*- coding: utf-8 -*-
"""
Created on Sun Jul 12 13:51:52 2015

@author: Stefan
"""

from pysiral.errorhandler import FileIOErrorHandler
from pysiral.esa.header import (ESAProductHeader, ESAScienceDataSetDescriptors)
from pysiral.cryosat2.l1b_mds_def import cryosat2_get_mds_def
from pysiral.cryosat2.functions import (parse_cryosat_l1b_filename,
                                        parse_cryosat_l1b_xml_header)
import re
from pathlib import Path
import numpy as np


class CryoSatL1B(object):
    """

    Purpose
    -------
    Container for CryoSat L1b Science Data including the xml header
    file (.HDR) and the header/data group file (.DBL). This class
    inherits all attributes and methods from **L1bData**.

    Applicable Documents
    --------------------
    - CRYOSAT Ground Segment, Instrument Processing Facility L1B,
      Products Specification Format, [PROD-FMT], CS-RS-ACS-GS-5106
      (issue used: 6.3, Date: 10/02/2015)

    Usage
    -----
    After initialisation of the class the filename of a .DBL file
    has to be supplied. Invoking the ``parse`` method will start
    the parsing of both the .HDR and .DBL files::

        l1b = CryoSatL1B()
        l1b.filename = filename
        l1b.parse()
        status = l1b.get_status()


    Header Attributes
    -----------------
    Available after ``parse`` method is called. Before that all are of type
    ``None``

    + xmlh
        Content of the xml header file (.HDR)
        The content is currently only saved as a nested OrderedDict
        object. This may change when the xml header information
        will be actually used.

    + mph
        Main Product Header block of the .DBL file. The attribute
        ``mph`` is of type **CS2L1bBaseHeader**.
        The mph fields consists of the parameters: name (tag), value,
        unit and datatype (dtype).

        Accessing fields:
            Each field can be either accessed directly. Example::

                orbit = l1b.mph.abs_orbit

            or with the `get_by_fieldname` method::

                orbit = l1b.mph.get_by_fieldname("abs_orbit")

            A list of all header field types is accessible in the attribute
            ``l1b.mph.field_list``.

        Units:
            The units are stored in a dictionary ``l1b.mph.unit_dict``
            where the keys are the field names::

                unit = l1b.mph.unit_dict["abs_orbit"]

            If no information on the unit is given in the MPH, the unit is
            of type ``None`` .

        Daty Types:
            The data type dictionary is mainly a debug parameter and
            contains the data types that have been used for converting
            the raw strings of the header into variables. Currently
            only types ``int32`` and ``float32`` are used for header
            number values. The default value is ``str``.
            The conversion is based on a specific list of data types for
            each header field. This list is located in the class
            definitions of **CS2L1bMainProductHeader** and
            **CS2L1bSpecificProductHeader** in ``pysiral/l1bfile.py``.

            The datatypes can be accessed like the units::

                dtype = l1b.mph.dtype_dict["abs_orbit"]

        Notes:
            - A quickview of the fields is given by
              ``print l1b.mph``
            - by convention all field names are lower case
            - No unit conversion is done as of yet if the unit contains
              a scaling (e.g. "10^-2%")

    + sph
        Specific Product Header block of the .DBL file. The attribute
        ``sph`` is of type `CS2L1bBaseHeader`. Accessing fields is
        identical to the main product header (see ``mph``).

    + dsd
        Data Set Descriptors in the .DBL file as an object of type
        **CS2L1bScienceDataSetDescriptors**.

        Each DSD in the dbl file is represented as an attribute of ``dsd`` that
        is of type ``dict``. The keys of the dictionary are given by the
        field names of each DSD, namely::

            ds_offset
            ds_tpye
            ds_size
            num_dsr
            filename

       A list of all DSD's is given in the string list ``l1b.dsd.field_list``.
       The information for each DSD can be accessed by directly calling the
       dictionary. E.g.::

           tide_file = l1b.dsd.pole_tide_file["filename"]



    Data Attributes
    ---------------


    Changelog
    ---------

    """

    _VALID_BASELINES = ["baseline-b", "baseline-c"]
    _VALID_RADAR_MODES = ["sar", "sin"]

    def __init__(self, read_header_only=False, raise_on_error=False):

        # Error Handling
        self._init_error_handling(raise_on_error)
        self._baseline = None
        self._radar_mode = None
        self._filename_header = None
        self._filename_product = None
        self._header_only = read_header_only
        self.xmlh = None
        self.mph = None
        self.sph = None
        self.dsd = None

    @property
    def filename(self):
        return self._filename_product

    @filename.setter
    def filename(self, filename):
        """ Save and validate filenames for header and product file """
        # Test if valid file first
        self._error.file_undefined = not Path(filename).is_file()
        if self._error.file_undefined:
            return
        # Split filenames in product and header file
        self._filename_product = filename
        self._filename_header = Path(filename).with_suffix(".HDR")

    @property
    def baseline(self):
        return self._baseline

    @property
    def radar_mode(self):
        return self._radar_mode

    def parse_header(self):
        """ Parse the content of the L1B file """
        # Validate input and either return or raise when input not ok
        if self._error.test_errors():
            self._error.validate()
            return
        # First detect the baseline from the file name
        self._detect_filetype()
        # Parse the xml header file
        try:
            self._parse_header_file()
            self._parse_product_header()
        except:
            self._error.io_failed = True

    def parse_mds(self):
        # Parse the product file
        with open(self._filename_product, "rb") as self._fh:
            self._parse_mds()

    def get_status(self):
        return self._error.test_errors()

    def post_processing(self, unpack=True, ocog=False):
        if unpack:
            self._unpack()
            self._trim()

    def _init_error_handling(self, raise_on_error):
        self._error = FileIOErrorHandler()
        self._error.raise_on_error = raise_on_error
        self._error.file_undefined = True

    def _detect_filetype(self):
        """ Detect and validate the baseline """
        info = parse_cryosat_l1b_filename(self._filename_product)
        self._baseline = "baseline-%s" % info.baseline[0].lower()
        self._radar_mode = info.radar_mode
        # Validate
        if self._baseline not in self._VALID_BASELINES:
            self._error.format_not_supported = True
        if self._radar_mode not in self._VALID_RADAR_MODES:
            self._error.format_not_supported = True

    def _parse_header_file(self):
        self.xmlh = parse_cryosat_l1b_xml_header(self._filename_header)

    def _parse_product_header(self):
        """
        Reads the content of *L1B*.DBL files
            - main product header (mph)
            - specific product header (sph)
            - data set descriptors (dsd)
            - (msd)
        """
        with open(self._filename_product, "r") as self._fh:
            self._parse_mph()
            self._parse_sph()
            self._parse_dsd()

    def _parse_mph(self):
        """
        Reads the main product header (mph) of a CryoSat-2 L1b file
        """
        # Save the mph information in its own class
        self.mph = CS2L1bMainProductHeader()
        self._read_header_lines(self.mph)

    def _parse_sph(self):
        """
        Reads the main product header (mph) of a CryoSat-2 L1b file
        """
        # Save the mph information in its own class
        self.sph = CS2L1bSpecificProductHeader()
        self._read_header_lines(self.sph)

    def _parse_dsd(self):
        """ Reads the Data Set Descriptors dsd's in the L1b header """
        self.dsd = ESAScienceDataSetDescriptors()
        n_dsd_lines = self.dsd.get_num_lines(self.mph.num_dsd)
        for i in np.arange(n_dsd_lines+1):
            line = self._fh.readline()
            self.dsd.parse_line(line)

    def _parse_mds(self):
        """ Read the data blocks """
        # Just reopened the file in binary mode -
        # > get start byte and number of data set records
        l1b_data_set_name = self._get_l1b_data_set_name()
        data_set_descriptor = self.dsd.get_by_fieldname(l1b_data_set_name)
        startbyte = int(data_set_descriptor["ds_offset"])
        self.n_msd_records = int(data_set_descriptor["num_dsr"])
        # Set the file pointer
        self._fh.seek(startbyte)
        # Get the parser
        self.mds_definition = cryosat2_get_mds_def(
            self._radar_mode, self._baseline, self.n_msd_records)
        mds_parser = self.mds_definition.get_mds_parser()
        # Parser the binary part of the .DBL file
        self.mds = mds_parser.parse(self._fh.read(mds_parser.sizeof()))

    def _get_l1b_data_set_name(self):
        radar_mode = self._radar_mode
        if radar_mode == "sin":
            radar_mode = "sarin"
        return "sir_l1b_"+radar_mode

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

    def _unpack(self):
        # Unpack the multiple record groups
        groups = self.mds_definition.get_multiple_block_groups()
        for group in groups:
            content = np.array(
                [[record[group]] for record in self.mds]).flatten()
            setattr(self, group, content)
        # Unpack the single record groups and replicate by number of
        # multiple rec
        groups = self.mds_definition.get_single_block_groups()
        for group in groups:
            content = np.array([record[group] for record in self.mds])
            setattr(self, group, np.repeat(
                content, self.mds_definition.n_blocks))

    def _trim(self):
        """
        Look for empty records at the end of the unpacked records
        (use source_sequence counter as identifier)
        """
        unpacked_groups = self.mds_definition.get_multiple_block_groups()
        unpacked_groups.extend(self.mds_definition.get_single_block_groups())
        ssc = np.array(
            [record["source_sequence_counter"] for record in self.time_orbit])
        no_zero_list = np.where(ssc != 0)[0]
        if len(no_zero_list) == 0:
            return
        for group in unpacked_groups:
            records = getattr(self, group)
            setattr(self, group, records[no_zero_list])


class CS2L1bMainProductHeader(ESAProductHeader):
    """
    Container for the Main Product Header of CryoSat-2 L1B science data
    """

    # Datatypes for automatic conversion
    # A field that is in here is converted to an 32bit integer
    _INT_LIST = [
        "PHASE", "CYCLE",  "REL_ORBIT", "ABS_ORBIT", "SAT_BINARY_TIME",
        "CLOCK_STEP", "LEAP_SIGN", "LEAP_ERR", "PRODUCT_ERR", "TOT_SIZE",
        "SPH_SIZE", "NUM_DSD", "DSD_SIZE", "NUM_DATA_SETS", "CRC",
        "ABS_ORBIT_START", "ABS_ORBIT_STOP", "L0_PROC_FLAG"]

    # A field that is in here is converted to an 32bit float
    _FLOAT_LIST = [
        "REL_TIME_ASC_NODE_START", "DELTA_UT1", "X_POSITION", "Y_POSITION",
        "Z_POSITION", "X_VELOCITY", "Y_VELOCITY", "Z_VELOCITY",
        "L0_PROCESSING_QUALITY", "L0_PROC_THRESH", "L0_GAPS_FLAG",
        "L0_GAPS_NUM", "OPEN_OCEAN_PERCENT", "CLOSE_SEA_PERCENT",
        "CONTINENT_ICE_PERCENT", "LAND_PERCENT", "L1B_PROD_STATUS",
        "L1B_PROC_FLAG", "L1B_PROCESSING_QUALITY", "L1B_PROC_THRESH",
        "REL_TIME_ASC_NODE_STOP", "EQUATOR_CROSS_LONG", "START_LAT",
        "START_LONG", "STOP_LAT", "STOP_LONG"]

    def __init__(self):
        super(CS2L1bMainProductHeader, self).__init__()

    def last_field(self, line):
        return re.search("CRC=", line)


class CS2L1bSpecificProductHeader(ESAProductHeader):
    """
    Container for the Specific Product Header of CryoSat-2 L1B science data
    """

    # Datatypes for automatic conversion
    # A field that is in here is converted to an 32bit integer
    _INT_LIST = ["ABS_ORBIT_START", "ABS_ORBIT_STOP", "L0_PROC_FLAG",
                 "L0_GAPS_FLAG", "L0_GAPS_NUM", "L1B_PROD_STATUS",
                 "L1B_PROC_FLAG", ]

    # A field that is in here is converted to an 32bit float
    _FLOAT_LIST = ["REL_TIME_ASC_NODE_START", "REL_TIME_ASC_NODE_STOP",
                   "EQUATOR_CROSS_LONG", "START_LAT", "START_LONG",
                   "STOP_LAT", "STOP_LONG", "L0_PROCESSING_QUALITY",
                   "L0_PROC_THRESH", "OPEN_OCEAN_PERCENT",
                   "CLOSE_SEA_PERCENT", "CONTINENT_ICE_PERCENT",
                   "LAND_PERCENT", "L1B_PROCESSING_QUALITY",
                   "L1B_PROC_THRESH"]

    def __init__(self):
        super(CS2L1bSpecificProductHeader, self).__init__()

    def last_field(self, line):
        return re.search("L1B_PROC_THRESH=", line)
