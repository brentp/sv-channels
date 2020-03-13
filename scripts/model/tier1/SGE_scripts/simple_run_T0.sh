#!/bin/bash -x

USERDIR="/hpc/cog_bioinf/ridder/users/lsantuari"
CHANNELDIR=$USERDIR"/Processed/DeepSV/channel_data"

#Named after the truth set used
OUTPUTDIR=$USERDIR"/Processed/DeepSV/two_tier/svclassify"

SAMPLE_TRAINING="NA12878"
TRUTHSET_TRAINING=$USERDIR"/Datasets/truth_sets/NA12878/Personalis_1000_Genomes_deduplicated_deletions.bed"

SAMPLE_TEST="NA24385"
TRUTHSET_TEST=$USERDIR"/Datasets/GiaB/HG002_NA24385_son/NIST_SVs_Integration_v0.6/processed/HG002_SVs_Tier1_v0.6.PASS.vcf.gz"

CHANNELDIR_TRAINING=$CHANNELDIR"/"$SAMPLE_TRAINING
CHANNELDIR_TEST=$CHANNELDIR"/"$SAMPLE_TEST

WINDOW=200
SHIFT=0

CHRARRAY=(`seq 1 22` 'X' 'Y')
CHRLIST=${CHRARRAY[@]}

echo "Loading conda environment..."
conda activate mcfly

echo "Running T0_S1_generate_training_data..."

python ../T0_S1_generate_training_data.py positive \
    -chrlist $CHRLIST \
    -win $WINDOW \
    -truthset $TRUTHSET_TRAINING \
    -inputdir $CHANNELDIR_TRAINING \
    -output $OUTPUTDIR"/positive.npz"

python ../T0_S1_generate_training_data.py negative \
    -chrlist $CHRLIST \
    -win $WINDOW \
    -truthset $TRUTHSET_TRAINING \
    -inputdir $CHANNELDIR_TRAINING \
    -output $OUTPUTDIR"/negative.npz"

echo "Running T0_S2_train..."

python ../T0_S2_train.py \
    -positive $OUTPUTDIR"/positive.npz" \
    -negative $OUTPUTDIR"/negative.npz" \
    -output $OUTPUTDIR"/model.hdf5"

echo "Running T0_S3_scan_chromosome..."

for CHROMOSOME in ${CHRARRAY[@]}; do
#CHROMOSOME=1

    echo "Running chromosome "$CHROMOSOME"..."

    python ../T0_S3_scan_chromosome.py \
        -inputdir $CHANNELDIR_TEST \
        -window $WINDOW \
        -chr $CHROMOSOME \
        -shift $SHIFT \
        -model $OUTPUTDIR"/model.hdf5" \
        -output $OUTPUTDIR"/"$CHROMOSOME"_predictions.npz"

done

echo "Running T0_S4_compare..."

python ../T0_S4_compare.py \
    -truthset $TRUTHSET_TEST \
    -chrlist $CHRLIST \
    -win $WINDOW \
    -inputdirlist $OUTPUTDIR \
    -output $OUTPUTDIR"/results.csv" \
    -outputbed $OUTPUTDIR"/regions_of_interest.bed"