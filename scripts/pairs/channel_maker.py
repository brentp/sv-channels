# Imports

import argparse
import errno
import gzip
import logging
import os
import pickle
import statistics
from collections import defaultdict
from time import time

import bz2file
import numpy as np
import pyBigWig
import pysam
from functions import get_one_hot_sequence_by_list
from candidate_pairs import *

# import matplotlib.pyplot as plt

# Flag used to set either paths on the local machine or on the HPC
HPC_MODE = False

# Only clipped read positions supported by at least min_cr_support clipped reads are considered
min_cr_support = 3
# Window half length
win_hlen = 100
# Window size
win_len = win_hlen * 2


def get_chr_len(ibam, chrName):
    # check if the BAM file exists
    assert os.path.isfile(ibam)
    # open the BAM file
    bamfile = pysam.AlignmentFile(ibam, "rb")

    # Extract chromosome length from the BAM header
    header_dict = bamfile.header
    chrLen = [i['LN'] for i in header_dict['SQ'] if i['SN'] == chrName][0]

    return chrLen


def create_dir(directory):
    '''
    Create a directory if it does not exist. Raises an exception if the directory exists.
    :param directory: directory to create
    :return: None
    '''
    try:
        os.makedirs(directory)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise


def load_clipped_read_positions(sampleName, chrName):
    channel_dir = '/Users/lsantuari/Documents/Data/HPC/DeepSV/GroundTruth'

    vec_type = 'clipped_read_pos'
    print('Loading CR positions for Chr %s' % chrName)
    # Load files
    if HPC_MODE:
        fn = '/'.join((sampleName, vec_type, chrName + '_' + vec_type + '.pbz2'))
    else:
        fn = '/'.join((channel_dir, sampleName, vec_type, chrName + '_' + vec_type + '.pbz2'))
    with bz2file.BZ2File(fn, 'rb') as f:
        cpos = pickle.load(f)

    cr_pos = [elem for elem, cnt in cpos.items() if cnt >= min_cr_support]

    return cr_pos


def count_clipped_read_positions(cpos_cnt):
    '''

    :param cpos_cnt: dictionary of clipped read positions (keys) and counts of clipped reads per position (values) as
    returned by the clipped_read_pos.py script
    :return: None. Prints the number of clipped read positions with clipped read support greater than the integers
    specified in the range
    '''
    for i in range(0, 5):
        logging.info('Number of positions with at least %d clipped reads: %d' %
                     (i + 1, len([k for k, v in cpos_cnt.items() if v > i])))


def get_mappability_bigwig():
    mappability_file = "/hpc/cog_bioinf/ridder/users/lsantuari/Datasets/Mappability/GRCh37.151mer.bw" if HPC_MODE \
        else "/Users/lsantuari/Documents/Data/GEM/GRCh37.151mer.bw"
    bw = pyBigWig.open(mappability_file)

    return bw


def load_bam(ibam):
    # check if the BAM file exists
    assert os.path.isfile(ibam)
    # open the BAM file
    return pysam.AlignmentFile(ibam, "rb")


def get_chr_len_dict(ibam):
    bamfile = load_bam(ibam)
    # Extract chromosome length from the BAM header
    header_dict = bamfile.header

    chrLen = {i['SN']: i['LN'] for i in header_dict['SQ']}
    return chrLen


def load_channels(sample, chr_list):
    prefix = ''
    channel_names = ['candidate_pairs', 'clipped_reads', 'clipped_read_distance',
                     'coverage', 'split_read_distance']

    channel_data = defaultdict(dict)

    for chrom in chr_list:
        logging.info('Loading data for Chr%s' % chrom)
        for ch in channel_names:
            logging.info('Loading data for channel %s' % ch)
            suffix = '.npy.bz2' if ch == 'coverage' else '.pbz2'
            if HPC_MODE:
                filename = os.path.join(prefix, sample, ch, '_'.join([chrom, ch + suffix]))
            else:
                filename = ch + suffix
            assert os.path.isfile(filename)

            logging.info('Reading %s for Chr%s' % (ch, chrom))
            with bz2file.BZ2File(filename, 'rb') as f:
                if suffix == '.npy.bz2':
                    channel_data[chrom][ch] = np.load(f)
                else:
                    channel_data[chrom][ch] = pickle.load(f)
            logging.info('End of reading')

        # unpack clipped_reads
        channel_data[chrom]['read_quality'], channel_data[chrom]['clipped_reads'], \
        channel_data[chrom]['clipped_reads_inversion'], channel_data[chrom]['clipped_reads_duplication'], \
        channel_data[chrom]['clipped_reads_translocation'] = channel_data[chrom]['clipped_reads']

        # unpack split_reads
        channel_data[chrom]['split_read_distance'], \
        channel_data[chrom]['split_reads'] = channel_data[chrom]['split_read_distance']

    return channel_data


def channel_maker(ibam, chrom, sampleName, outFile):
    def check_progress(i, n_r, last_t):

        if i != 0 and not i % n_r:
            now_t = time()
            # print(type(now_t))
            logging.info("%d candidate pairs processed (%f pairs / s)" % (
                i,
                n_r / (now_t - last_t)))
            last_t = time()

    n_channels = 29
    bp_padding = 10

    channel_data = load_channels(sampleName, [chrom])

    bw_map = get_mappability_bigwig()

    candidate_pairs_chr = [sv for sv in channel_data[chrom]['candidate_pairs']
                           if sv.tuple[0].chr == sv.tuple[1].chr and sv.tuple[0].chr == chrom]

    channel_windows = np.zeros(shape=(len(candidate_pairs_chr),
                                      win_len * 2 + bp_padding, n_channels), dtype=np.uint32)

    # Consider a single sample
    sample_list = sampleName.split('_')

    # for sample in sample_list:

    # Log info every n_r times
    n_r = 10 ** 3
    # print(n_r)
    last_t = time()

    i = 0

    # dictionary of key choices
    direction_list = {'clipped_reads': ['left', 'right', 'D_left', 'D_right', 'I'],
                      'split_reads': ['left', 'right'],
                      'split_read_distance': ['left', 'right'],
                      'clipped_reads_inversion': ['before', 'after'],
                      'clipped_reads_duplication': ['before', 'after'],
                      'clipped_reads_translocation': ['opposite', 'same'],
                      'clipped_read_distance': ['forward', 'reverse']
                      }

    positions = []
    for sv in candidate_pairs_chr:
        bp1, bp2 = sv.tuple
        positions.extend(list(range(bp1.pos - win_hlen, bp1.pos + win_hlen)) +
                         list(range(bp2.pos - win_hlen, bp2.pos + win_hlen)))
    positions = np.array(positions)

    idx = np.arange(win_len)
    idx2 = np.arange(start=win_len + bp_padding, stop=win_len * 2 + bp_padding)
    idx = np.concatenate((idx, idx2), axis=0)

    channel_index = 0

    for current_channel in ['coverage', 'read_quality',
                            'clipped_reads', 'split_reads',
                            'clipped_reads_inversion', 'clipped_reads_duplication',
                            'clipped_reads_translocation',
                            'clipped_read_distance', 'split_read_distance']:

        logging.info("Adding channel %s" % current_channel)

        if current_channel == 'coverage' or current_channel == 'read_quality':

            payload = channel_data[chrom][current_channel][positions]
            payload.shape = channel_windows[:, idx, channel_index].shape
            channel_windows[:, idx, channel_index] = payload
            channel_index += 1

        elif current_channel in ['clipped_reads', 'split_reads',
                               'clipped_reads_inversion', 'clipped_reads_duplication',
                               'clipped_reads_translocation']:
            for split_direction in direction_list[current_channel]:

                channel_pos = set(positions) & set(channel_data[chrom][current_channel][split_direction].keys())
                payload = [ channel_data[chrom][current_channel][split_direction][pos] if pos in channel_pos else 0 \
                 for pos in positions ]
                payload = np.array(payload)
                payload.shape = channel_windows[:, idx, channel_index].shape
                channel_windows[:, idx, channel_index] = payload
                channel_index += 1

        elif current_channel == 'clipped_read_distance':
            for split_direction in direction_list[current_channel]:
                for clipped_arrangement in ['left', 'right', 'all']:

                    channel_pos = set(positions) & \
                                  set(channel_data[chrom][current_channel][split_direction][clipped_arrangement].keys())
                    payload = [ statistics.median(
                        channel_data[chrom][current_channel][split_direction][clipped_arrangement][pos]) \
                                    if pos in channel_pos else 0 for pos in positions ]
                    payload = np.array(payload)
                    payload.shape = channel_windows[:, idx, channel_index].shape
                    channel_windows[:, idx, channel_index] = payload
                    channel_index += 1

        elif current_channel == 'split_read_distance':
            for split_direction in direction_list[current_channel]:

                channel_pos = set(positions) & \
                              set(channel_data[chrom][current_channel][split_direction].keys())
                payload = [ statistics.median(
                    channel_data[chrom][current_channel][split_direction][pos]) \
                                if pos in channel_pos else 0 for pos in positions ]
                payload = np.array(payload)
                payload.shape = channel_windows[:, idx, channel_index].shape
                channel_windows[:, idx, channel_index] = payload
                channel_index += 1

    logging.info("Adding channel %s" % current_channel)

    nuc_list = ['A', 'T', 'C', 'G', 'N']

    payload = get_one_hot_sequence_by_list(chrom, positions, HPC_MODE)
    payload.shape = channel_windows[:, idx, channel_index].shape
    channel_windows[:, idx, channel_index:channel_index+len(nuc_list)] = payload
    channel_index += len(nuc_list)

    logging.info("Adding channel %s" % current_channel)

    payload = np.array([ bw_map.values(chrom, p, p+1) for p in positions ])
    payload.shape = channel_windows[:, idx, channel_index].shape
    channel_windows[:, idx, channel_index] = payload

    #
    # for sv in candidate_pairs_chr:
    #
    #     check_progress(i, n_r, last_t)
    #
    #     bp1, bp2 = sv.tuple
    #     chr1, start1, end1 = bp1.chr, bp1.pos - win_hlen, bp1.pos + win_hlen
    #     chr2, start2, end2 = bp2.chr, bp2.pos - win_hlen, bp2.pos + win_hlen
    #
    #     # index
    #     channel_index = 0
    #
    #     for current_channel in ['coverage', 'read_quality',
    #                             'clipped_reads', 'split_reads',
    #                             'clipped_read_distance', 'split_read_distance']:
    #
    #     #for current_channel in ['coverage', 'read_quality']:
    #
    #         if current_channel == 'coverage' or current_channel == 'read_quality':
    #
    #             payload = np.concatenate((channel_data[chr1][current_channel][start1:end1],
    #                                       np.zeros(shape=bp_padding, dtype=np.uint32),
    #                                       channel_data[chr2][current_channel][start2:end2]), axis=0)
    #             channel_windows[i, :, channel_index] = payload
    #             del payload
    #
    #             channel_index += 1
    #
    #         elif current_channel == 'clipped_reads' or current_channel == 'split_reads':
    #
    #             split_direction_list = ['left', 'right', 'D_left', 'D_right', 'I'] \
    #                 if current_channel == 'clipped_reads' else ['left', 'right']
    #
    #             for split_direction in split_direction_list:
    #                 for pos in range(start1, end1):
    #                     if pos in channel_data[chr1][current_channel][split_direction].keys():
    #                         channel_windows[i, pos - start1, channel_index] = \
    #                             channel_data[chr1][current_channel][split_direction][pos]
    #                 for pos in range(start2, end2):
    #                     if pos in channel_data[chr2][current_channel][split_direction].keys():
    #                         channel_windows[i, (win_len+bp_padding) + (pos-start2), channel_index] = \
    #                             channel_data[chr2][current_channel][split_direction][pos]
    #                 channel_index += 1
    #
    #         elif current_channel == 'clipped_reads_inversion' or \
    #             current_channel == 'clipped_reads_duplication':
    #
    #             for mate_direction in ['before', 'after']:
    #                 for pos in range(start1, end1):
    #                     if pos in channel_data[chr1][current_channel][mate_direction].keys():
    #                         channel_windows[i, pos - start1, channel_index] = \
    #                             channel_data[chr1][current_channel][mate_direction][pos]
    #                 for pos in range(start2, end2):
    #                     if pos in channel_data[chr2][current_channel][mate_direction].keys():
    #                         channel_windows[i, (win_len+bp_padding) + (pos-start2), channel_index] = \
    #                             channel_data[chr2][current_channel][mate_direction][pos]
    #                 channel_index += 1
    #
    #         elif current_channel == 'clipped_reads_translocation':
    #
    #             for orientation in ['opposite', 'same']:
    #                 for pos in range(start1, end1):
    #                     if pos in channel_data[chr1][current_channel][orientation].keys():
    #                         channel_windows[i, pos - start1, channel_index] = \
    #                             channel_data[chr1][current_channel][orientation][pos]
    #                 for pos in range(start2, end2):
    #                     if pos in channel_data[chr2][current_channel][orientation].keys():
    #                         channel_windows[i, (win_len+bp_padding) + (pos-start2), channel_index] = \
    #                             channel_data[chr2][current_channel][orientation][pos]
    #                 channel_index += 1
    #
    #         elif current_channel == 'clipped_read_distance':
    #
    #             for direction in ['forward', 'reverse']:
    #                 for clipped_arrangement in ['left', 'right', 'all']:
    #                     for pos in range(start1, end1):
    #                         if pos in channel_data[chr1][current_channel][direction][clipped_arrangement].keys():
    #                             channel_windows[i, pos-start1, channel_index] = \
    #                                 statistics.median(
    #                                 channel_data[chr1][current_channel][direction][clipped_arrangement][pos])
    #                     for pos in range(start2, end2):
    #                         if pos in channel_data[chr2][current_channel][direction][clipped_arrangement].keys():
    #                             channel_windows[i, (win_len+bp_padding) + (pos-start2), channel_index] = \
    #                                 statistics.median(
    #                                 channel_data[chr2][current_channel][direction][clipped_arrangement][pos])
    #                     channel_index += 1
    #
    #         elif current_channel == 'split_read_distance':
    #
    #             for split_direction in ['left', 'right']:
    #                 for pos in range(start1, end1):
    #                     if pos in channel_data[chr1][current_channel][split_direction].keys():
    #                         channel_windows[i, pos - start1, channel_index] = \
    #                             statistics.median(
    #                                 channel_data[chr1][current_channel][split_direction][pos])
    #                 for pos in range(start2, end2):
    #                     if pos in channel_data[chr2][current_channel][split_direction].keys():
    #                         channel_windows[i, (win_len + bp_padding) + (pos - start2), channel_index] = \
    #                             statistics.median(
    #                                 channel_data[chr2][current_channel][split_direction][pos])
    #                 channel_index += 1
    #
    #     # one hot encoding
    #     nuc_list = ['A', 'T', 'C', 'G', 'N']
    #     for idx, nuc in enumerate(nuc_list, start=channel_index):
    #         channel_windows[i, :win_len, channel_index] = get_one_hot_sequence(
    #             chr1, start1, end1, nuc, HPC_MODE)
    #         channel_windows[i, win_len + bp_padding:, channel_index] = get_one_hot_sequence(
    #             chr2, start2, end2, nuc, HPC_MODE)
    #         channel_index = idx + 1
    #
    #     # mappability
    #     channel_windows[i, :win_len, channel_index] = bw_map.values(chr1, start1, end1)
    #     channel_windows[i, win_len + bp_padding:, channel_index] = bw_map.values(chr2, start2, end2)
    #
    #     i += 1

    logging.info("channel_windows shape: %s" % str(channel_windows.shape))

    # Save the list of channel vstacks
    with gzip.GzipFile(outFile, "w") as f:
        np.save(file=f, arr=channel_windows)
    f.close()


def main():
    '''
    Main function for parsing the input arguments and calling the channel_maker function
    :return: None
    '''

    # Default BAM file for testing
    # On the HPC
    # wd = '/hpc/cog_bioinf/ridder/users/lsantuari/Datasets/DeepSV/'+
    #   'artificial_data/run_test_INDEL/samples/T0/BAM/T0/mapping'
    # inputBAM = wd + "T0_dedup.bam"
    # Locally
    wd = '/Users/lsantuari/Documents/Data/HPC/DeepSV/Artificial_data/run_test_INDEL/BAM/'
    inputBAM = wd + "T1_dedup.bam"

    parser = argparse.ArgumentParser(description='Create channels from saved data')
    parser.add_argument('-b', '--bam', type=str,
                        default=inputBAM,
                        help="Specify input file (BAM)")
    parser.add_argument('-c', '--chr', type=str, default='17',
                        help="Specify chromosome")
    parser.add_argument('-o', '--out', type=str, default='channel_maker.npy.gz',
                        help="Specify output")
    parser.add_argument('-s', '--sample', type=str, default='NA12878',
                        help="Specify sample")
    parser.add_argument('-l', '--logfile', default='channel_maker.log',
                        help='File in which to write logs.')

    args = parser.parse_args()

    logfilename = args.logfile
    FORMAT = '%(asctime)s %(message)s'
    logging.basicConfig(
        format=FORMAT,
        filename=logfilename,
        filemode='w',
        level=logging.INFO)

    t0 = time()

    channel_maker(ibam=args.bam, chrom=args.chr, sampleName=args.sample, outFile=args.out)

    # print('Elapsed time channel_maker_real on BAM %s and Chr %s = %f' % (args.bam, args.chr, time() - t0))
    print('Elapsed time channel_maker_real = %f' % (time() - t0))


if __name__ == '__main__':
    main()
