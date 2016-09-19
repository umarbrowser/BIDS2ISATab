#!/usr/bin/env python
#

# import modules used here -- sys is a very standard one
from __future__ import print_function
import argparse
import logging
from collections import OrderedDict
from glob import glob
import os
from os.path import exists, join as opj, split as psplit
import sys

import nibabel
import json
import pandas as pd


# map column titles to ontology specs
# based on this info the appropriate additonal column in the ISATab tables are
# generated
ontology_term_map = {
    # qualitative information
    "Characteristics[organism]": {
        'homo sapiens': ('NCBITAXON', 'NCBITaxon:9606'),
    },
    "Characteristics[organism part]": {
        'brain': ('UBERON', 'UBERON:0000955'),
    },
    "Characteristics[sex]": {
        'female': ('PATO', 'PATO:0000383'),
        'male': ('PATO', 'PATO:0000384'),
    },
    "Characteristics[handedness]": {
        'right':  ('PATO', 'PATO:0002203'),
        'left': ('PATO', 'PATO:0002202'),
        'ambidextrous': ('PATO', 'PATO:0002204'),
    },
    # quantitative information
    "Characteristics[age at scan]": ('UO', 'UO:0000036', 'year'),
    "Parameter Value[resolution]": ('UO', 'UO:0000016', 'millimeter'),
    "Parameter Value[repetition time]": ('UO', 'UO:0000010', 'second'),
    "Parameter Value[magnetic field strength]": ('UO', 'UO:0000228', 'tesla'),
    "Parameter Value[flip angle]": ('UO', 'UO:0000185', 'degree'),
    "Parameter Value[echo time]": ('UO', 'UO:0000010', 'second'),
    # no associated term, keep but leave untouched
    "Parameter Value[instrument name]": None,
    "Parameter Value[instrument manufacturer]": None,
    "Parameter Value[coil type]": None,
    "Parameter Value[sequence]": None,
    'Protocol REF': None,
    'Sample Name': None,
    'Assay Name': None,
    'Raw Data File': None,
# Deal with the following
#    'Parameter Value[Modality]',
#    'Parameter Value[Resolution]',
#    'Parameter Value[PhaseEncodingDirection]',
#    'Parameter Value[HardcopyDeviceSoftwareVersion]',
#    'Parameter Value[SliceTiming]',
#    'Parameter Value[ParallelReductionFactorInPlane]',
#    'Parameter Value[StoryDepthSegmentRatings]',
#    'Parameter Value[CogAtlasID]',
#    'Parameter Value[TaskDescription]',
#    'Parameter Value[SliceEncodingDirection]',
#    'Parameter Value[ParallelAcquisitionTechnique]',
#    'Parameter Value[CogPOID]',
}

# translate from what we find in BIDS or a DICOM dump into the
# names that ScientificData prefers
# matching will be done on lower case string
# add any synonyms or additions as entries toi this dictionary
parameter_name_map = {
    "manufacturermodelname": "instrument name",
    "manufacturer": "instrument manufacturer",
    "receivecoilname": "coil type",
    "magneticfieldstrength": "magnetic field strength",
    "receivecoilname": "coil type",
    "echotime": "echo time",
    "repetitiontime": "repetition time",
    "flipangle": "flip angle",
    "pulsesequencetype": "sequence",
}


def get_metadata_for_nifti(bids_root, path):

    sidecarJSON = path.replace(".nii.gz", ".json")

    pathComponents = psplit(sidecarJSON)
    filenameComponents = pathComponents[-1].split("_")
    sessionLevelComponentList = []
    subjectLevelComponentList = []
    topLevelComponentList = []
    ses = None
    sub = None

    for filenameComponent in filenameComponents:
        if filenameComponent[:3] != "run":
            sessionLevelComponentList.append(filenameComponent)
            if filenameComponent[:3] == "ses":
                ses = filenameComponent
            else:
                subjectLevelComponentList.append(filenameComponent)
                if filenameComponent[:3] == "sub":
                    sub = filenameComponent
                else:
                    topLevelComponentList.append(filenameComponent)

    topLevelJSON = opj(bids_root, "_".join(topLevelComponentList))
    potentialJSONs = [topLevelJSON]

    subjectLevelJSON = opj(bids_root, sub, "_".join(subjectLevelComponentList))
    potentialJSONs.append(subjectLevelJSON)

    if ses:
        sessionLevelJSON = opj(bids_root, sub, ses, "_".join(sessionLevelComponentList))
        potentialJSONs.append(sessionLevelJSON)

    potentialJSONs.append(sidecarJSON)

    merged_param_dict = {}
    for json_file_path in potentialJSONs:
        if exists(json_file_path):
            param_dict = json.load(open(json_file_path, "r"))
            merged_param_dict.update(param_dict)

    return merged_param_dict


def get_chainvalue(chain, src):
    try:
        for key in chain:
            src = src[key]
        return src
    except KeyError:
        return None


def get_keychains(d, dest, prefix):
    if isinstance(d, dict):
        for item in d:
            dest = get_keychains(d[item], dest, prefix + [item])
    else:
        if d and not (d == 'UNDEFINED'):
            # ignore empty stuff
            dest = dest.union((tuple(prefix),))
    return dest


def _get_study_df(bids_directory):
    subject_ids = []
    study_dict = OrderedDict()
    for file in glob(opj(bids_directory, "sub-*")):
        if os.path.isdir(file):
            subject_ids.append(psplit(file)[-1][4:])
    subject_ids.sort()
    study_dict["Source Name"] = subject_ids
    study_dict["Characteristics[organism]"] = "Homo sapiens"
    study_dict["Term Source REF"] = "NCBITAXON"
    study_dict["Term Accession Number"] = "NCBITaxon:9606"
    study_dict["Characteristics[organism part]"] = "brain"
    study_dict["Protocol REF"] = "Participant recruitment"
    study_dict["Sample Name"] = subject_ids
    df = pd.DataFrame(study_dict)

    participants_file = opj(bids_directory, "participants.tsv")
    if exists(participants_file):
        participants_df = pd.read_csv(participants_file, sep="\t")
        participants_df.rename(
            columns={'participant_id': "Sample Name"},
            inplace=True)
        participants_df["Sample Name"] = \
            [s[4:] for s in list(participants_df["Sample Name"])]
        for col in participants_df.columns.tolist():
            if col != "Sample Name":
                participants_df.rename(
                    columns={col: "Comment[%s]" % col},
                    inplace=True)
        df = pd.merge(
            df,
            participants_df,
            left_on="Sample Name",
            right_on="Sample Name")
    return df


def _describe_mri_file(fpath, bids_directory):
    fname = psplit(fpath)[-1]
    info = {
        'sample_name': fname.split("_")[0][4:],
        'assay_name': fname.split(".")[0],
        'raw_filepath': fpath[len(bids_directory):],
        'type': fname.split("_")[-1].split(".")[0]
    }
    info['other_fields'] = get_metadata_for_nifti(bids_directory, fname)
    if not exists(fpath):
        # this could happen in the case of a dead symlink in,
        # e.g., a git-annex repo
        logging.warn(
            "cannot extract meta data from '{}'".format(fpath))
        return info

    header = nibabel.load(fpath).get_header()
    info['resolution'] = "x".join([str(i) for i in header.get_zooms()[:3]])
    info['resolutions_units'] = header.get_xyzt_units()[0]
    if len(header.get_zooms()) > 3:
        info['rts'] = header.get_zooms()[3]
        info['rts_units'] = header.get_xyzt_units()[1]
    return info


def _get_mri_assay_df(bids_directory):
    assay_dict = OrderedDict()
    assay_dict["Protocol REF"] = "Magnetic Resonance Imaging"

    collector_dict = {
        'sample_name': [],
        'assay_name': [],
        'raw_filepath': [],
        'type': [],
        'other_fields': [],
        'resolution': [],
        'resolutions_units': [],
        'rts': [],
        'rts_units': [],
    }

    for fname in glob(opj(bids_directory, "sub-*", "*", "sub-*.nii.gz")) + \
            glob(opj(bids_directory, "sub-*", "ses-*", "*", "sub-*_ses-*.nii.gz")):
        finfo = _describe_mri_file(fname, bids_directory)
        for spec in collector_dict:
            fspec = finfo.get(spec, None)
            collector_dict[spec].append(fspec)

    # map gathered info into assay dict
    for spec_out, spec_in in (
            # order is important!!
            ("Sample Name", "sample_name"),
            ("Parameter Value[Modality]", 'type'),
            ("Parameter Value[Resolution]", 'resolution'),
            ("Unit", 'resolutions_units')):
        assay_dict[spec_out] = collector_dict[spec_in]

    # record order of parameters; needs to match order in above loop
    mri_par_names = ["Resolution", "Modality"]

    # determine the union of any additional fields found for any file
    new_fields = set()
    for d in collector_dict['other_fields']:
        new_fields = get_keychains(d, new_fields, [])
    # create a parameter column for each of them
    for field in new_fields:
        # deal with nested structures by concatenating the field names
        field_name = ':'.join(field)
        # normalize parameter names
        field_name = parameter_name_map.get(field_name.lower(), field_name)
        # final column ID
        column_id = "Parameter Value[{}]".format(field_name)
        assay_dict[column_id] = []
        # and fill with content from files
        for d in collector_dict['other_fields']:
            assay_dict[column_id].append(get_chainvalue(field, d))

    # TODO: check whether this loop can be merged with the similar one above
    for spec_out, spec_in in (
            # order is important!!
            ("Assay Name", 'assay_name'),
            ("Raw Data File", 'raw_filepath')):
        assay_dict[spec_out] = collector_dict[spec_in]

    df = pd.DataFrame(assay_dict)
    df = df.sort_values(['Assay Name'])
    return df, mri_par_names  # TODO investigate necessity for 2nd return value


def _get_investigation_template(bids_directory, mri_par_names):
    this_path = opj(os.path.realpath(__file__))
    template_path = opj(
        *(psplit(this_path)[:-1] + ("i_investigation_template.txt", )))
    investigation_template = open(template_path).read()

    title = psplit(bids_directory)[-1]

    if exists(opj(bids_directory, "dataset_description.json")):
        with open(opj(bids_directory, "dataset_description.json"), "r") \
                as description_dict_fp:
            description_dict = json.load(description_dict_fp)
            if "Name" in description_dict:
                title = description_dict["Name"]

    investigation_template = investigation_template.replace(
        "[TODO: TITLE]", title)
    investigation_template = investigation_template.replace(
        "[TODO: MRI_PAR_NAMES]", ";".join(mri_par_names))
    return investigation_template


def _drop_from_df(df, drop):
    if drop is None:
        return
    elif drop == 'unknown':
        # remove anything that isn't white-listed
        drop = [k for k in df.keys() if not k in ontology_term_map]
    elif isinstance(drop, (tuple, list)):
        # is list of parameter names to drop
        drop = ['Parameter Value[{}]'.format(d) for d in drop]

    # at this point drop is some iterable
    # filter assay table and take out matching parameters
    for k in df.keys():
        if k in drop:
            print('dropping %s from output' % k)
            df.drop(k, axis=1, inplace=True)


def _df_with_ontology_info(df):
    items = []
    for col, val in df.iteritems():
        # put each input column in the output unconditionally
        items.append((col, val))
        # check if we know something about this column
        term_map = ontology_term_map.get(col, None)
        if term_map is None:
            # we know nothing more
            continue
        if isinstance(term_map, tuple):
            # this is quantitative information -> 4-column group
            items.append(('Unit', term_map[2]))
            items.append(('Term Source REF', term_map[0]))
            items.append(('Term Accession Number', term_map[1]))
        elif isinstance(term_map, tuple):
            # this is qualitative information -> 3-column group
            refs = []
            acss = []
            for v in val:
                ref, acs = term_map.get(v, (None, None))
                refs.append(ref)
                acss.append(acs)
                if ref is None:
                    logging.warn("unknown value '{}' for '{}' (known: {})".format(
                        v, col, term_map.keys()))
            items.append(('Term Source REF', refs))
            items.append(('Term Accession Number', acss))
    return pd.DataFrame.from_items(items)


def extract(
        bids_directory,
        output_directory,
        drop_parameter=None):
    if not exists(output_directory):
        logging.info(
            "creating output directory at '{}'".format(output_directory))
        os.makedirs(output_directory)

    # generate: s_study.txt
    study_df = _get_study_df(bids_directory)
    study_df = _df_with_ontology_info(study_df)
    study_df.to_csv(
        opj(output_directory, "s_study.txt"),
        sep="\t",
        index=False)

    # generate: a_assay.txt
    mri_assay_df, mri_par_names = _get_mri_assay_df(bids_directory)
    _drop_from_df(mri_assay_df, drop_parameter)
    mri_assay_df = _df_with_ontology_info(mri_assay_df)
    mri_assay_df.to_csv(
        opj(output_directory, "a_assay.txt"),
        sep="\t",
        index=False)

    # generate: i_investigation.txt
    investigation_template = _get_investigation_template(
        bids_directory, mri_par_names)
    with open(opj(output_directory, "i_investigation.txt"), "w") as fp:
        fp.write(investigation_template)


def _get_cmdline_parser():
    class MyParser(argparse.ArgumentParser):
        def error(self, message):
            sys.stderr.write('error: %s\n' % message)
            self.print_help()
            sys.exit(2)

    parser = MyParser(
        description="BIDS to ISA-Tab converter.",
        fromfile_prefix_chars='@')
    # TODO Specify your real parameters here.
    parser.add_argument(
        "bids_directory",
        help="Location of the root of your BIDS compatible directory",
        metavar="BIDS_DIRECTORY")
    parser.add_argument(
        "output_directory",
        help="Directory where ISA-TAB files will be stored",
        metavar="OUTPUT_DIRECTORY")
    parser.add_argument(
        "-v",
        "--verbose",
        help="increase output verbosity",
        action="store_true")
    parser.add_argument(
        "--keep-unknown",
        help="""by default only explicitely white-listed parameters and
        characteristics are considered. This option will force inclusion of
        any discovered information. See --drop-parameter for additional
        tuning.""",
        action='store_true')
    parser.add_argument(
        "-d",
        "--drop-parameter",
        help="""list of parameters to ignore when composing the assay table. See
        the generated table for column IDs to ignore. For example, to remove
        column 'Parameter Value[time:samples:ContentTime]', specify
        `--drop-parameter time:samples:ContentTime`. Only considered together
        with --keep-unknown.""")
    return parser


def main():
    parser = _get_cmdline_parser()
    args = parser.parse_args()

    # Setup logging
    if args.verbose:
        loglevel = logging.DEBUG
    else:
        loglevel = logging.INFO

    logging.basicConfig(format="%(levelname)s: %(message)s", level=loglevel)

    extract(
        args.bids_directory,
        args.output_directory,
        args.drop_parameter if args.keep_unknown else 'unknown',
    )
    print("Metadata extraction complete.")


if __name__ == '__main__':
    main()
