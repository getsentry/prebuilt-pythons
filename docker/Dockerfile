FROM ubuntu:18.04
RUN : \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install \
        -y --no-install-recommends \
        gcc \
        libbz2-dev \
        libdb-dev \
        libexpat1-dev \
        libffi-dev \
        libgdbm-dev \
        liblzma-dev \
        libncursesw5-dev \
        libreadline-dev \
        libsqlite3-dev \
        libssl-dev \
        make \
        patchelf \
        python3.8-distutils \
        python3.8-venv \
        uuid-dev \
        xz-utils \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
# match minimum target for this script (macos python3 is 3.8)
RUN python3.8 -m venv /venv
ENV BUILD_BINARY_IN_CONTAINER=1 PATH=/venv/bin:$PATH
