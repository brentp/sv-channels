# Imports
import argparse
import re
import pysam
from pysam import VariantFile
from collections import Counter
from intervaltree import IntervalTree

from collections import defaultdict
import numpy as np
import gzip
import os, errno
from time import time
import json
import logging
from functions import *
from label_classes import SVRecord

with open('parameters.json', 'r') as f:
    config = json.load(f)

HPC_MODE = config["DEFAULT"]["HPC_MODE"]


def get_chr_list():

    chrlist = list(map(str, range(1, 23)))
    chrlist.extend(['X'])
    #chrlist = ['17']

    return chrlist


def read_vcf(invcf):

    # Check file existence
    assert os.path.isfile(invcf), invcf + ' not found!'
    # Dictionary with chromosome keys to store SVs
    sv_list = []

    vcf_in = VariantFile(invcf, 'r')
    for rec in vcf_in.fetch():

        var = SVRecord(rec, 'gridss')

        chrom1 = var.chrom
        pos1_start = var.start + var.cipos[0]
        pos1_end = var.start + var.cipos[1] + 1

        chrom2 = var.chrom2
        pos2_start = var.end + var.ciend[0]
        pos2_end = var.end + var.ciend[1] + 1
        svtype = var.svtype

        if svtype == "DEL":
            sv_list.append((
                chrom1, pos1_start, pos1_end,
                chrom2, pos2_start, pos2_end,
                svtype
            ))

    logging.info('{} SVs'.format(len(sv_list)))

    return sv_list


def read_bedpe(inbedpe):

    # Check file existence
    assert os.path.isfile(inbedpe), inbedpe + ' not found!'
    # Dictionary with chromosome keys to store SVs
    sv_list = []

    with(open(inbedpe, 'r')) as bed:
        for line in bed:
            columns = line.rstrip().split("\t")
            chrom1, pos1_start, pos1_end = str(columns[0]), int(columns[1]), int(columns[2])
            chrom2, pos2_start, pos2_end = str(columns[3]), int(columns[4]), int(columns[5])
            svtype = columns[10]

            if svtype == "TYPE:DELETION":
                svtype = "DEL"

            if svtype == "DEL":
                sv_list.append((
                    chrom1, pos1_start, pos1_end,
                    chrom2, pos2_start, pos2_end,
                    svtype
                ))

    logging.info('{} SVs'.format(len(sv_list)))

    return sv_list


def filter_bedpe(inbedpe, sv_id_list, outDir):

    # Check file existence
    assert os.path.isfile(inbedpe), inbedpe + ' not found!'
    # Dictionary with chromosome keys to store SVs
    logging.info('{} SVs to filter out'.format(len(sv_id_list)))
    lines_to_keep = []

    with(open(inbedpe, 'r')) as bed:
        for line in bed:
            columns = line.rstrip().split("\t")
            chrom1, pos1_start, pos1_end = str(columns[0]), int(columns[1]), int(columns[2])
            chrom2, pos2_start, pos2_end = str(columns[3]), int(columns[4]), int(columns[5])
            svtype = columns[10]
            svtype = "DEL" if svtype == "TYPE:DELETION" else svtype

            sv_id = '_'.join((svtype, chrom1, str(pos1_start), chrom2, str(pos2_start)))

            if svtype == "DEL" and sv_id not in sv_id_list:
                lines_to_keep.append(line)

        fileout = os.path.join(outDir, 'uncaptured_SVs.bedpe')
        logging.info('Writing {}'.format(fileout))

        with(open(fileout, 'w')) as fout:
            for line in lines_to_keep:
                fout.write(line)

        logging.info('{} SVs written'.format(len(lines_to_keep)))


def read_survivor_simsv_output(insur):

    # Check file existence
    assert os.path.isfile(insur), insur + ' not found!'
    # Dictionary with chromosome keys to store SVs
    sv_list = []

    with(open(insur, 'r')) as bed:
        for line in bed:
            columns = line.rstrip().split("\t")
            chrom1, pos1_start, pos1_end = str(columns[0]), int(columns[1]) - 1, int(columns[1])
            chrom2, pos2_start, pos2_end = str(columns[2]), int(columns[3]) - 1, int(columns[3])
            svtype = columns[4]

            if svtype == "DEL":
                sv_list.append((
                    chrom1, pos1_start, pos1_end,
                    chrom2, pos2_start, pos2_end,
                    svtype
                ))

    logging.info('{} SVs'.format(len(sv_list)))

    return sv_list


def filter_survivor_output(insur, sv_id_list, outDir):

    # Check file existence
    assert os.path.isfile(insur), insur + ' not found!'

    logging.info('{} SVs to filter out'.format(len(sv_id_list)))
    lines_to_keep = []

    with(open(insur, 'r')) as bed:
        for line in bed:
            columns = line.rstrip().split("\t")
            chrom1, pos1_start, pos1_end = str(columns[0]), int(columns[1]) - 1, int(columns[1])
            chrom2, pos2_start, pos2_end = str(columns[2]), int(columns[3]) - 1, int(columns[3])
            svtype = columns[4]

            sv_id = '_'.join((svtype, chrom1, str(pos1_start), chrom2, str(pos2_start)))

            if svtype == "DEL" and sv_id not in sv_id_list:
                lines_to_keep.append(line)

    fileout = os.path.join(outDir, 'uncaptured_SVs.sur')
    logging.info('Writing {}'.format(fileout))

    with(open(fileout, 'w')) as fout:
        for line in lines_to_keep:
            fout.write(line)

    logging.info('{} SVs written'.format(len(lines_to_keep)))


def overlap(sv_list, cpos_list_right, cpos_list_left, win_hlen, ground_truth, outDir):
    '''

    :param sv_list: list, list of SVs
    :param cr_pos: list, list of clipped read positions
    :return: list, list of clipped read positions whose window completely overlap either the CIPOS interval
    or the CIEND interval
    '''

    def make_gtrees_from_svlist(sv_list):

        logging.info('Building SV GenomicTrees...')
        # Tree with windows for candidate positions
        trees_start = defaultdict(IntervalTree)
        trees_end = defaultdict(IntervalTree)

        # Populate tree
        for sv in sv_list:
            chrom1, pos1_start, pos1_end, chrom2, pos2_start, pos2_end, svtype = sv
            sv_id = '_'.join((svtype, chrom1, str(pos1_start), chrom2, str(pos2_start)))

            trees_start[chrom1][pos1_start:pos1_end] = (svtype, sv_id)
            trees_end[chrom2][pos2_start:pos2_end] = (svtype, sv_id)

        # print('Tree start')
        # for k in trees_start.keys():
        #     print('{} : {}'.format( k, len(trees_start[k])))
        # print('Tree end')
        # for k in trees_end.keys():
        #     print('{} : {}'.format( k, len(trees_end[k])))

        return trees_start, trees_end

    def search_tree_with_cpos(cpos, trees):

        logging.info('Searching SV GenomicTrees with candidate positions...')

        lookup = []

        # Log info every n_r times
        n_r = 10 ** 6
        last_t = time()

        for i, p in enumerate(cpos, start=1):

            if not i % n_r:
                now_t = time()
                # print(type(now_t))
                logging.info("%d candidate positions processed (%f positions / s)" % (
                    i,
                    n_r / (now_t - last_t)))
                last_t = time()

            chrom1, pos1 = p
            lookup.append(trees[chrom1][pos1 - win_hlen:pos1 + win_hlen + 1])

        return lookup

    trees_start, trees_end = make_gtrees_from_svlist(sv_list)

    logging.info('Start positions')
    lookup_start = search_tree_with_cpos(cpos_list_right, trees_start)
    logging.info('End positions')
    lookup_end = search_tree_with_cpos(cpos_list_left, trees_end)

    # print([l for l in lookup_start if len(l) > 0])
    # print([l for l in lookup_end if len(l) > 0])

    labels = dict()

    sv_covered = set()

    def fill_labels(labels, sv_covered, cpos_list, lookup, pos_type):

        for p, lu in zip(cpos_list, lookup):

            chrom1, pos1 = p
            pos_id = '_'.join((chrom1, str(pos1)))

            l1 = len(lu)

            if l1 == 1:

                lu_elem_start, lu_elem_end, lu_elem_data = lu.pop()
                lu_elem_svtype, lu_elem_svid = lu_elem_data

                if pos1 - win_hlen <= lu_elem_start and lu_elem_end <= pos1 + win_hlen:

                    # logging.info(
                    #     'Chr1:{}\tpos1:{}-{}\tChr2:{}\tpos2:{}-{}'.format(
                    #         chrom1, pos1 - win_hlen, pos1 + win_hlen, chrom2, pos2 - win_hlen, pos2 + win_hlen
                    #     )
                    # )
                    # logging.info(
                    #     'LookUp_start:{}-{}_{}\tLookUp_end:{}-{}_{}'.format(
                    #         lu_start_elem_start, lu_start_elem_end, lu_start_elem_data,
                    #         lu_end_elem_start, lu_end_elem_end, lu_end_elem_data
                    #     )
                    # )
                    # if pos1 in np.arange(lu_start_elem_start-2, lu_start_elem_end+2) and \
                    #         pos2 in np.arange(lu_end_elem_start-2, lu_end_elem_end+2):

                    sv_covered.add(lu_elem_svid)
                    labels[pos_id] = lu_elem_svtype + '_' + pos_type

                    # else:
                    #     sv_covered.add(lu_start_elem_svid)
                    #     labels[pos_id] = 'UK_overlap_not_matching'
                else:
                    labels[pos_id] = 'UK_single_partial'

            elif l1 > 1:

                lu_set = set()

                for s in lu:
                    lu_elem_start, lu_elem_end, lu_elem_data = s
                    lu_elem_svtype, lu_elem_svid = lu_elem_data
                    lu_set.add(lu_elem_svid)

                sv_covered = sv_covered | lu_set
                labels[pos_id] = 'UK_multiple'

            else:
                labels[pos_id] = 'noDEL'

        return labels, sv_covered

    labels, sv_covered = fill_labels(labels, sv_covered, cpos_list_right, lookup_start, pos_type='start')
    labels, sv_covered = fill_labels(labels, sv_covered, cpos_list_left, lookup_end, pos_type='end')

    logging.info(Counter(labels.values()))
    sv_coverage = int(len(sv_covered) / len(sv_list) * 100)
    logging.info('SV coverage: {}/{}={}%'.format(len(sv_covered),
                                                 len(sv_list),
                                                 sv_coverage))

    filename, file_extension = os.path.splitext(ground_truth)

    if file_extension == '.bedpe':
        # print(sv_covered)
        filter_bedpe(ground_truth, sv_covered, outDir)
    elif file_extension == '.sur':
        filter_survivor_output(ground_truth, sv_covered, outDir)

    return labels


# Get labels
def get_labels(ibam, sampleName, win_len, ground_truth, channelDataDir, outFile, outDir):
    logging.info('running {}'.format(sampleName))

    def make_gtrees_from_truth_set(truth_set, file_ext):

        # Using IntervalTree for interval search
        trees_start = defaultdict(IntervalTree)
        trees_end = defaultdict(IntervalTree)

        if file_ext == 'VCF':

            for var in sv_list:
                # cipos[0] and ciend[0] are negative in the VCF file
                id_start = var.svtype + '_start'
                id_end = var.svtype + '_end'

                assert var.start <= var.end, "Start: " + str(var.start) + " End: " + str(var.end)

                # logging.info('var start -> %s:%d CIPOS: (%d, %d)' % (
                # var.chrom, var.start, var.cipos[0], var.cipos[1])
                # )
                # logging.info('var end -> %s:%d CIEND: (%d, %d)' % (
                # var.chrom2, var.end, var.ciend[0], var.ciend[1])
                # )

                trees_start['chr' + var.chrom][var.start + var.cipos[0]:var.start + var.cipos[1] + 1] = id_start
                trees_end['chr' + var.chrom2][var.end + var.ciend[0]:var.end + var.ciend[1] + 1] = id_end

        elif file_ext in ['BEDPE', 'SUR']:

            for sv in sv_list:
                chrom1, pos1_start, pos1_end, chrom2, pos2_start, pos2_end, svtype = sv

                id_start = svtype + '_start'
                id_end = svtype + '_end'

                trees_start['chr' + chrom1][pos1_start:pos1_end + 1] = id_start
                trees_end['chr' + chrom2][pos2_start:pos2_end + 1] = id_end

        return trees_start, trees_end

    # windows half length
    win_hlen = int(int(win_len) / 2)
    # get chromosome lengths
    chr_dict = get_chr_len_dict(ibam)
    chrlist = get_chr_list()

    cpos_list_right, cpos_list_left = load_all_clipped_read_positions(
        sampleName, win_hlen, chr_dict, channelDataDir, 'CR'
    )

    # Keep only positions that can be used to create windows
    chr_len_dict = get_chr_len_dict(ibam)

    def filter_cpos_list(cpos_list):
        return [(chrom, pos) for chrom, pos in cpos_list
                 if chrom in chrlist and
                 win_hlen <= pos <= chr_len_dict[chrom] - win_hlen]

    cpos_list_right = filter_cpos_list(cpos_list_right)
    cpos_list_left = filter_cpos_list(cpos_list_left)

    filename, file_extension = os.path.splitext(ground_truth)
    if file_extension == '.bedpe':
        sv_list = read_bedpe(ground_truth)
    elif file_extension == '.sur':
        sv_list = read_survivor_simsv_output(ground_truth)
    elif file_extension == '.vcf' or file_extension == '.gz':
        sv_list = read_vcf(ground_truth)

    # Get overlap of candidate positions with all SV breakpoints (all 4 SV callers)
    # crpos_all_sv = get_crpos_overlap_with_sv_callsets(sv_dict, cr_pos_dict)

    # filename, file_extension = os.path.splitext(ground_truth)
    # trees_start, trees_end = make_gtrees_from_truth_set(sv_list, file_extension.upper())

    labels = overlap(sv_list, cpos_list_right, cpos_list_left, win_hlen, ground_truth, outDir)

    with gzip.GzipFile(outFile, 'wb') as fout:
        fout.write(json.dumps(labels).encode('utf-8'))


def main():
    '''
    Label windows according to truth set
    :return: None
    '''

    wd = '/Users/lsantuari/Documents/Data/HPC/DeepSV/Artificial_data/run_test_INDEL/BAM/'
    inputBAM = wd + "T1_dedup.bam"

    parser = argparse.ArgumentParser(description='Create labels')
    parser.add_argument('-b', '--bam', type=str,
                        default=inputBAM,
                        help="Specify input file (BAM)")
    parser.add_argument('-l', '--logfile', type=str, default='labels.log',
                        help="Specify log file")
    parser.add_argument('-s', '--sample', type=str, default='NA24385',
                        help="Specify sample")
    parser.add_argument('-w', '--window', type=str, default=200,
                        help="Specify window size")
    parser.add_argument('-gt', '--ground_truth', type=str,
                        # default=os.path.join('/Users/lsantuari/Documents/Data/germline/NA24385',
                        #                      'NIST_SVs_Integration_v0.6/processed/HG002_SVs_Tier1_v0.6.PASS.vcf.gz'),
                        default=os.path.join('/Users/lsantuari/Documents/Data/germline/NA24385',
                                             'NIST_SVs_Integration_v0.6/HG002_SVs_Tier1_v0.6.vcf.gz'),
                        # default=os.path.join('/Users/lsantuari/Documents/Data/germline/CHM/Huddleston2016',
                        #                     'structural_variants/CHM1_CHM13_pseudodiploid_SVs.vcf.gz'),
                        # default=os.path.join('/Users/lsantuari/Documents/Data/svclassify',
                        #                      'Personalis_1000_Genomes_deduplicated_deletions.bedpe'),
                        # default=os.path.join('/Users/lsantuari/Documents/External_GitHub/sv_benchmark/',
                        #                      'input.na12878/lumpy-Mills2011-call-set.nanosv.sorted.bedpe'),
                        # default=os.path.join('/Users/lsantuari/Documents/Data/HPC/DeepSV/Artificial_data',
                        #                      'run_test_INDEL/SV/chr17_INDEL.sur'),
                        # default=os.path.join('/Users/lsantuari/Documents/Data/germline/NA12878/SV',
                        #                      'Filtered/gridss.vcf'),
                        help="Specify ground truth VCF/BEDPE file")
    parser.add_argument('-o', '--out', type=str, default='labels.json.gz',
                        help="Specify output")
    parser.add_argument('-p', '--outputpath', type=str,
                        default='/Users/lsantuari/Documents/Processed/channel_maker_output',
                        help="Specify output path")

    args = parser.parse_args()

    # Log file
    cmd_name = 'labels_win' + str(args.window) + '_sw'
    output_dir = os.path.join(args.outputpath, args.sample, cmd_name)
    create_dir(output_dir)
    logfilename = os.path.join(output_dir, args.logfile)
    output_file = os.path.join(output_dir, args.out)

    FORMAT = '%(asctime)s %(message)s'
    logging.basicConfig(
        format=FORMAT,
        filename=logfilename,
        filemode='w',
        level=logging.INFO)

    t0 = time()

    get_labels(ibam=args.bam,
               sampleName=args.sample,
               win_len=args.window,
               ground_truth=args.ground_truth,
               channelDataDir=args.outputpath,
               outFile=output_file,
               outDir=output_dir,
               )

    logging.info('Elapsed time making labels = %f' % (time() - t0))


if __name__ == '__main__':
    main()
