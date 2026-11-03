#!/bin/bash

export IMAGENAME="s1_l1n_processor"
export IMAGETAG=${1}

echo "=== Building image: ${IMAGENAME} ==="

podman build -t ${IMAGENAME}:${IMAGETAG} .

echo "=== Saving image to disk ==="

FILE_TAG="${IMAGETAG//./_}"

podman save --format docker-archive ${IMAGENAME}:${IMAGETAG} | gzip > dist/${IMAGENAME}_${FILE_TAG}.tar.gz

echo "=== Docker image building completed: ${IMAGENAME}:${IMAGETAG} ==="
