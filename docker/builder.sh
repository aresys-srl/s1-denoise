#!/bin/bash

export IMAGENAME="s1_l1n_processor"
export IMAGETAG=${1}

echo "=== Building image: ${IMAGENAME} ==="

docker build -t ${IMAGENAME}:${IMAGETAG} .

echo "=== Saving image to disk ==="

FILE_TAG="${IMAGETAG//./_}"

docker save -o ${IMAGENAME}_${FILE_TAG}.tar ${IMAGENAME}:${IMAGETAG}
xz -T4 -f "${IMAGENAME}_${FILE_TAG}.tar"

echo "=== Docker image building completed: ${IMAGENAME}:${IMAGETAG} ==="
