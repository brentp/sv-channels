#!/usr/bin/env bash

set -xe

# check input arg(s)
if [ $# -lt "3" ]; then
  echo "Usage: $0 [SCHEDULER {gridengine,slurm}] [BAM file] [SEQID...]"
  exit 1
fi

# set variables
SCH=$1  # scheduler type
BAM=$(realpath -s "$2")
BASE_DIR=$(dirname "$BAM")
SAMPLE=$(basename "$BAM" .bam)
SEQ_IDS=(${@:3})
PREFIX="${BASE_DIR}/${SAMPLE}"
TWOBIT="${PREFIX}.2bit"
BIGWIG="${PREFIX}.bw"
BEDPE="${PREFIX}.bedpe"
WORK_DIR=scripts/genome_wide
#NUMEXPR_MAX_THREADS=128  # required by py-bcolz
STARTTIME=$(date +%s)
JOBS=() # array of job IDs
JOBS_LOG=jobs.json # job accounting log
RTIME=10  # runtime in minutes
STIME=1 # sleep X minutes
MY_ENV=wf # conda env

submit () {  # submit a job via Xenon CLI
  xenon -v scheduler $SCH --location local:// submit \
    --name "${1}-${2}" --cores-per-task 1 --inherit-env --max-run-time $RTIME \
    --working-directory . --stderr stderr-%j.log --stdout stdout-%j.log "$3"
}

monitor () {  # monitor a job via Xenon CLI
  xenon -v --json scheduler $SCH --location local:// list --identifier $1
}

# activate conda env
eval "$(conda shell.bash hook)"
conda activate $MY_ENV
conda list

# submit jobs to output "channel" files (*.json.gz and *.npy.gz)
cd $WORK_DIR

p=clipped_reads
JOB="python $p.py -b $BAM -c "${SEQ_IDS[*]}" -o $p.json.gz -p . -l $p.log"
JOB_ID=$(submit $p all "$JOB")
JOBS+=($JOB_ID)

p=clipped_read_pos
JOB="python $p.py -b '$BAM' -c "${SEQ_IDS[*]}" -o $p.json.gz -p . -l $p.log"
JOB_ID=$(submit $p all "$JOB")
JOBS+=($JOB_ID)

p=split_reads
JOB="python $p.py -b '$BAM' -c "${SEQ_IDS[*]}" -o $p.json.gz -ob $p.bedpe.gz \
  -p . -l $p.log"
JOB_ID=$(submit $p all "$JOB")
JOBS+=($JOB_ID)

for s in "${SEQ_IDS[@]}"; do  # per chromosome
  p=clipped_read_distance
  JOB="python $p.py -b '$BAM' -c $s -o $p.json.gz -p . -l $p.log"
  JOB_ID=$(submit $p $s "$JOB")
  JOBS+=($JOB_ID)

  p=snv
  JOB="python $p.py -b '$BAM' -c $s -t '$TWOBIT' -o $p.npy -p . -l $p.log"
  JOB_ID=$(submit $p $s "$JOB")
  JOBS+=($JOB_ID)

  p=coverage
  JOB="python $p.py -b '$BAM' -c $s -o $p.npy -p . -l $p.log"
  JOB_ID=$(submit $p $s "$JOB")
  JOBS+=($JOB_ID)
done

# wait until the jobs are done
for j in "${JOBS[@]}"; do
  while true; do
    [[ $(monitor $j | jq \
    '.statuses | .[] | select(.done==true)') ]] && \
      break || sleep ${STIME}m
  done
done

# generate chromosome arrays from the channels as well as label window pairs
for s in "${SEQ_IDS[@]}"; do
  p=chr_array
  JOB="python $p.py -b '$BAM' -c $s -t '$TWOBIT' -m '$BIGWIG' -o $p.npy \
    -p . -l $p.log"
  JOB_ID=$(submit $p $s "$JOB")
  JOBS+=($JOB_ID)
done

# wait until the jobs are done
for j in "${JOBS[@]}"; do
  while true; do
    [[ $(monitor $j | jq '.statuses | .[] | select(.done==true)') ]] && \
      break || sleep ${STIME}m
  done
done

p=label_window_pairs_on_split_read_positions
JOB="python $p.py -b '$BAM' -w 200 -gt '$BEDPE' -s 'DEL' -o $p.json.gz -p . -l $p.log"
JOB_ID=$(submit $p $s "$JOB")
JOBS+=($JOB_ID)

p=label_window_pairs_on_svcallset
JOB="python $p.py -b '$BAM' -w 200 -gt '$BEDPE' -s 'DEL' -sv '${BASE_DIR}/gridss' \
  -o $p.json.gz -p . -l $p.log"
JOB_ID=$(submit $p $s "$JOB")
JOBS+=($JOB_ID)

# generate window pairs
p=create_window_pairs
JOB="python $p.py -b '$BAM' -c "${SEQ_IDS[*]}" -sv gridss -w 200 -p . -l $p.log"
JOB_ID=$(submit $p all "$JOB")
JOBS+=($JOB_ID)

# train/test models
p=train_model_with_fit
JOB="python $p.py --test_sample . --training_sample . -k 3 -p . -s 'DEL' -l $p.log"
JOB_ID=$(submit $p all "$JOB")
JOBS+=($JOB_ID)

# wait until the jobs are done
while true; do
  [[ $(monitor ${JOBS[-1]} | jq '.statuses | .[] | select(.done==true)') ]] \
    && break || sleep ${STIME}m
done

# wait until the jobs are done
while true; do
  [[ $(monitor ${JOBS[-1]} | jq '.statuses | .[] | select(.done==true)') ]] \
    && break || sleep ${STIME}m
done

ENDTIME=$(date +%s)
echo "Processing ${#JOBS[@]} jobs took $((ENDTIME - STARTTIME)) sec to complete."

# collect job accounting info
for j in "${JOBS[@]}"; do
  monitor $j >> $JOBS_LOG
done
cat $JOBS_LOG

# output logs in std{out,err}-[jobid].log
echo "----------"
echo "Log files:"
for f in $(find -type f -name "*.log"); do
  echo "### $f ###"
  cat $f
done

# list "channel" files
echo "-------------"
echo "Output files:"
#ls
find -type f -name "*.json.gz" | grep "." || exit 1
find -type f -name "*.npy.gz" | grep "." || exit 1

# exit with non-zero if there are failed jobs
[[ $(jq ".statuses | .[] | select(.done==true and .exitCode!=0)" $JOBS_LOG) ]] \
  && exit 1 || exit 0
