FROM ubuntu:22.04
ARG TARGETARCH

ENV LC_ALL C.UTF-8

ENV BUILD_PACKAGES "build-essential libtool autotools-dev automake pkg-config bsdmainutils curl git ca-certificates ccache bison"

ENV PACKAGES "python3-zmq libevent-dev libboost-filesystem-dev libboost-test-dev libdb5.3++-dev libminiupnpc-dev libzmq3-dev libqrencode-dev systemtap-sdt-dev bpfcc-tools bpftrace"

RUN apt-get update && \
    apt-get install --no-install-recommends --no-upgrade -qq $PACKAGES $BUILD_PACKAGES

WORKDIR /tapyrus-core

COPY . ./depends
RUN  if [ "$TARGETARCH" = "arm64" ]; then BUILD_HOST="aarch64-linux-gnu"; else BUILD_HOST="x86_64-pc-linux-gnu"; fi && \
     make HOST=$BUILD_HOST -j"$(($(nproc)+1))" -C depends
