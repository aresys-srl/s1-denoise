FROM docker.io/rockylinux/rockylinux:10

ENV LANG=C.utf8
ENV PROCESSOR_HOME=/opt/s1_l1n_processor
ENV S1_L0IMPORTQC_PATH=$PROCESSOR_HOME/bin/Sentinel1L0ImportQC

RUN dnf -y install python3 python3-devel python3-pip \
    && dnf clean all

RUN python3 -m pip install --upgrade pip setuptools wheel hatchling

WORKDIR $PROCESSOR_HOME

COPY denoise ./denoise
COPY bin ./bin
COPY wheels ./wheels
COPY pyproject.toml .
COPY README.md .
COPY LICENSE .

RUN pip3 install --find-links=wheels wheels/arepyextras_eo_products-1.2.14-py3-none-any.whl
RUN pip3 install --no-cache-dir -e .[cli]
RUN chmod +x $PROCESSOR_HOME/bin/Sentinel1L0ImportQC

ENTRYPOINT ["denoise"]