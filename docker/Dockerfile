ARG ARCH
FROM quay.io/pypa/manylinux_2_24_${ARCH}
RUN : \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install \
        -y --no-install-recommends \
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
        patchelf \
        uuid-dev \
        xz-utils \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
# match minimum target for this script (macos python3 is 3.8)
ENV BUILD_BINARY_IN_CONTAINER=1 PATH=/opt/python/cp38-cp38/bin:$PATH
