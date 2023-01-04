#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2022-2023 Technology Innovation Institute (TII)
#
# SPDX-License-Identifier: Apache-2.0

# pylint: disable=fixme

""" Module for generating SBOMs in various formats """

import uuid
import logging
import json

import pandas as pd
import numpy as np
from packageurl import PackageURL
from sbomnix.nix import Store
from sbomnix.utils import (
    LOGGER_NAME,
    df_to_csv_file,
)

# from nixgraph.graph import NixDependencies


###############################################################################

_LOG = logging.getLogger(LOGGER_NAME)

###############################################################################


class SbomDb:
    """SbomDb allows generating SBOMs in various formats"""

    def __init__(self, nix_path, runtime=False, meta_path=None):
        _LOG.debug("")
        self.store = Store(nix_path, runtime)
        self.df_sbomdb = self._generate_sbomdb(meta_path)

    def _generate_sbomdb(self, meta_path):
        _LOG.debug("")
        df_store = self.store.to_dataframe()
        df_sbomdb = df_store
        if meta_path is not None:
            df_meta = _parse_json_metadata(meta_path)
            if _LOG.level <= logging.DEBUG:
                df_to_csv_file(df_meta, "meta.csv")
            # Join based on package name including the version number
            df_sbomdb = df_store.merge(
                df_meta,
                how="left",
                left_on=["name"],
                right_on=["name"],
                suffixes=["", "_meta"],
            )
        df_sbom = df_sbomdb.replace(np.nan, "", regex=True)
        return df_sbom.drop_duplicates(subset="store_path", keep="first")

    def to_cdx(self, cdx_path):
        """Export sbomdb to cyclonedx json file"""
        target_path = self.store.get_target_drv_path()
        cdx = {}
        cdx["bomFormat"] = "CycloneDX"
        cdx["specVersion"] = "1.3"
        cdx["version"] = 1
        cdx["serialNumber"] = f"urn:uuid:{uuid.uuid4()}"
        cdx["metadata"] = {}
        tool = {}
        tool["vendor"] = "TII"
        tool["name"] = "sbomnix"
        tool["version"] = "0.1.0"
        cdx["metadata"]["tools"] = []
        cdx["metadata"]["tools"].append(tool)
        cdx["components"] = []
        for row in self.df_sbomdb.itertuples():
            component = _df_row_to_cdx_component(row)
            if row.store_path == target_path:
                cdx["metadata"]["component"] = component
            else:
                cdx["components"].append(component)

        with open(cdx_path, "w", encoding="utf-8") as outfile:
            json_string = json.dumps(cdx, indent=2)
            outfile.write(json_string)
            _LOG.info("Wrote: %s", outfile.name)

    def to_csv(self, csv_path):
        """Export sbomdb to csv file"""
        df_to_csv_file(self.df_sbomdb, csv_path)


################################################################################

# CycloneDX


def _licenses_entry_from_row(row, column_name, cdx_license_type):
    """Parse license entries of type cdx_license_type from column_name"""
    licenses = []
    if column_name not in row._asdict():
        # Return empty list if column name is not in row
        return licenses
    license_str = getattr(row, column_name)
    if not license_str:
        # Return empty list if license string is empty
        return licenses
    # Parse the ";" separated licenses to cdx license format
    license_strings = license_str.split(";")
    for license_string in license_strings:
        license_dict = {"license": {cdx_license_type: license_string}}
        licenses.append(license_dict)
    return licenses


def _cdx_component_add_licenses(component, row):
    """Add licenses array to cdx component (if any)"""
    licenses = []
    # First, try reading the license in spdxid-format
    # TODO: spdxid license data from meta in many cases is not spdxids
    # but something else, therefore, skipping this for now:
    # licenses = licenses_entry_from_row(row, "meta_license_spdxid", "id")
    # If it fails, try reading the license short name
    if not licenses:
        licenses = _licenses_entry_from_row(row, "meta_license_short", "name")
    # Give up if pacakge does not have license information associated
    if not licenses:
        return
    # Otherwise, add the licenses entry
    component["licenses"] = licenses


def _df_row_to_cdx_component(row):
    """Convert one entry from df_sbomdb (row) to cdx component"""
    component = {}
    component["type"] = "application"
    component["bom-ref"] = row.store_path
    component["name"] = row.pname
    component["version"] = row.version
    purl = PackageURL(type="nix", name=row.pname, version=row.version)
    component["purl"] = str(purl)
    component["cpe"] = row.cpe
    _cdx_component_add_licenses(component, row)
    return component


###############################################################################

# Nix package metadata


def _parse_meta_entry(meta, key):
    """Parse the given key from the metadata entry"""
    if isinstance(meta, dict):
        ret = [meta.get(key, "")]
    elif isinstance(meta, list):
        ret = [x.get(key, "") if isinstance(x, dict) else x for x in meta]
    else:
        ret = [meta]
    return list(filter(None, ret))


def _parse_json_metadata(json_filename):
    """Parse package metadata from the specified json file"""

    with open(json_filename, "r", encoding="utf-8") as inf:
        _LOG.info('Loading meta info from "%s"', json_filename)
        json_dict = json.loads(inf.read())

        dict_selected = {}
        setcol = dict_selected.setdefault
        for nixpkg_name, pkg in json_dict.items():
            # generic package info
            setcol("nixpkgs", []).append(nixpkg_name)
            setcol("name", []).append(pkg.get("name", ""))
            setcol("pname", []).append(pkg.get("pname", ""))
            setcol("version", []).append(pkg.get("version", ""))
            # meta
            meta = pkg.get("meta", {})
            setcol("meta_homepage", []).append(meta.get("homepage", ""))
            setcol("meta_position", []).append(meta.get("position", ""))
            # meta.license
            meta_license = meta.get("license", {})
            license_short = _parse_meta_entry(meta_license, key="shortName")
            setcol("meta_license_short", []).append(";".join(license_short))
            license_spdx = _parse_meta_entry(meta_license, key="spdxId")
            setcol("meta_license_spdxid", []).append(";".join(license_spdx))
            # meta.maintainers
            meta_maintainers = meta.get("maintainers", {})
            emails = _parse_meta_entry(meta_maintainers, key="email")
            setcol("meta_maintainers_email", []).append(";".join(emails))

        return pd.DataFrame(dict_selected)


################################################################################
