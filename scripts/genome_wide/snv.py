import argparse
import logging
import os
from collections import Counter
from time import time

import numpy as np
import pysam
import twobitreader as twobit

from functions import *


def get_snvs(ibam, itwobit, chrName, max_coverage, outFile):

    def get_snv_number(query_seq_list, reference_base):

        reference_base = reference_base.upper()
        if len(query_seq_list) > 0 and reference_base != 'N':
            cnt = Counter(list(map(lambda x: x.upper(), query_seq_list)))
            return cnt['A'] + cnt['T'] + cnt['C'] + cnt['G'] - cnt[
                reference_base]
        else:
            return 0

    # Check if the BAM file in input exists
    assert os.path.isfile(ibam)

    # Load the BAM file
    bamfile = pysam.AlignmentFile(ibam, "rb")
    # Extract the header
    header_dict = bamfile.header
    # Get the chromosome length from the header
    chrLen = [i['LN'] for i in header_dict['SQ'] if i['SN'] == chrName][0]

    # Fetch reads over the entire chromosome between positions [0, chrLen]
    start_pos = 0
    stop_pos = chrLen

    reference_sequence = twobit.TwoBitFile(itwobit)

    snv_list = ['BQ', 'SNV']
    snv_array = np.zeros(shape=(len(snv_list), stop_pos), dtype=np.uint32)
    snv_dict = {v: n for n, v in enumerate(snv_list)}
    # print(snv_dict)
    # Print every n_r alignments processed
    n_r = 10**6
    # Record the current time
    last_t = time()

    for pileupcolumn in bamfile.pileup(chrName,
                                       start_pos,
                                       stop_pos,
                                       stepper='all'):
        # pileupcolumn.set_min_base_quality(0)
        # print("\ncoverage at base %s = %s" %
        #       (pileupcolumn.pos, pileupcolumn.nsegments))
        if 0 < pileupcolumn.nsegments < max_coverage and start_pos <= pileupcolumn.pos <= stop_pos:
            quals = pileupcolumn.get_query_qualities()
            if len(quals) > 0:
                snv_array[snv_dict['BQ'], pileupcolumn.pos] = np.median(
                    pileupcolumn.get_query_qualities())
            # snv_array[snv_dict['nALN'], pileupcolumn.pos] = pileupcolumn.get_num_aligned()
            # snv_array[snv_dict['nSEG'], pileupcolumn.pos] = pileupcolumn.nsegments
            try:

                query_seq_list = pileupcolumn.get_query_sequences()

                snv_number = get_snv_number(
                    query_seq_list,
                    reference_sequence[chrName][pileupcolumn.pos])

                snv_array[snv_dict['SNV'], pileupcolumn.pos] = snv_number/pileupcolumn.nsegments \
                    if pileupcolumn.nsegments != 0 else 0

            except AssertionError as error:
                # Output expected AssertionErrors.
                logging.info(error)
                logging.info('Position {}:{} has {} nsegments'.format(
                    chrName, pileupcolumn.pos, pileupcolumn.nsegments))
                continue

    # Write the output
    # snv_array = np.delete(snv_array, 2, 0)
    np.save(file=outFile, arr=snv_array)
    os.system('gzip -f ' + outFile)


def main():

    # Parse the arguments of the script
    parser = argparse.ArgumentParser(description='Get SNV info')
    parser.add_argument('-b',
                        '--bam',
                        type=str,
                        default='../../data/test.bam',
                        help="Specify input file (BAM)")
    parser.add_argument('-t',
                        '--twobit',
                        type=str,
                        default='../../data/test.2bit',
                        help="Specify input file (2bit)")
    parser.add_argument('-c',
                        '--chr',
                        type=str,
                        default='12',
                        help="Specify chromosome")
    parser.add_argument('-o',
                        '--out',
                        type=str,
                        default='snv.npy',
                        help="Specify output")
    parser.add_argument('-p',
                        '--outputpath',
                        type=str,
                        default='.',
                        help="Specify output path")
    parser.add_argument('-l',
                        '--logfile',
                        default='snv.log',
                        help='File in which to write logs.')
    parser.add_argument('-pb',
                        '--max_coverage',
                        type=int,
                        default=1000,
                        help='Consider only regions with coverage less than max_coverage to speed up the processing')

    args = parser.parse_args()

    # Log file

    cmd_name = 'snv'

    output_dir = os.path.join(args.outputpath, cmd_name)

    os.makedirs(output_dir, exist_ok=True)

    logfilename = os.path.join(output_dir, '_'.join((args.chr, args.logfile)))
    output_file = os.path.join(output_dir, '_'.join((args.chr, args.out)))

    FORMAT = '%(asctime)s %(message)s'
    logging.basicConfig(format=FORMAT,
                        filename=logfilename,
                        filemode='w',
                        level=logging.INFO)

    t0 = time()
    get_snvs(ibam=args.bam,
             itwobit=args.twobit,
             chrName=args.chr,
             max_coverage=args.max_coverage,
             outFile=output_file)
    logging.info('Time: SNVs on BAM %s and Chr %s: %f' % (args.bam, args.chr,
                                                          (time() - t0)))


if __name__ == '__main__':
    main()
