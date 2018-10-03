#!/bin/bash -x

# This script generate the channel data for the NoSV category of the Training data

# Path to the BAM files for the artificial data with the INDELs
SVMODE='INV'

# INPATH='/hpc/cog_bioinf/ridder/users/lsantuari/Datasets/DeepSV/artificial_data/WG/run_'$SVMODE'_500K/samples/'
INPATH='/hpc/cog_bioinf/ridder/users/lsantuari/Datasets/DeepSV/artificial_data/run_'$SVMODE'/samples/'

INARRAY=(${INPATH}'N1/BAM/N1/mapping/N1_dedup.bam' ${INPATH}'N2/BAM/N2/mapping/N2_dedup.bam')

# The NoSV category
NOSV1="N1"
NOSV2="N2"

SAMPLE_ARRAY=(${GERMLINE1} ${GERMLINE2} ${NOSV1} ${NOSV2})

CATEGORIES=('NoSV')

# Run single channel scripts (0) or ChannelMaker (1)
RUNALL=0

if [ $RUNALL == 0 ]; then
for CAT in ${CATEGORIES[@]}; do
	for i in 0 1; do
		for CHROMOSOME in 17; do
			for PRG in clipped_read_pos coverage clipped_read_distance clipped_reads split_read_distance; do
				BAM=${INARRAY[$i]}
				qsub -v CHRARG=$CHROMOSOME,BAMARG=$BAM,PRGARG=${PRG},OUTARG=$CAT'/'${OUTARRAY[$i]} channel_script_by_name.sge
			done
		done
	done
done

elif [ $RUNALL == 1 ]; then

# Output should be in the Tumor folder
i=0
# This BAM is only used to extract header information
BAM=${INARRAY[$i]}
# NoSV category
CAT=${CATEGORIES[$i]}

# ChannelMaker script to generate channel data for real data, in this case used for the NoSV category of the Training data
PRG='channel_maker_real_germline'

for CHROMOSOME in 17; do

	qsub -v CHRARG=$CHROMOSOME,BAMARG=$BAM,PRGARG=${PRG},OUTARG=$CAT'/'${OUTARRAY[$i]} make_channel.sge

done

fi
