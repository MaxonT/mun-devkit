# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: Apache-2.0
# The MUN Devkit build environment: Debian 13 (trixie), ARM64, with the
# compiler and the development packages of the libraries the runtime profile
# linux-arm64-gl-v0 provides on a console.
#
# Every input is pinned: the base image by digest, the packages by the dated
# Debian snapshot MUN OS's own image is built from (os/inputs.json in MUN OS:
# 20260926T000000Z). A program built here links against the same library
# versions a console of that MUN OS line has.
#
# The base image is newer than the snapshot, so its own packages are first
# brought to the snapshot's versions (the pin makes apt prefer them, even
# downwards): the whole environment is then of that date.
#
# Mesa's software renderer is installed so that a GL game can be run without a
# display in checks; it says nothing about a console's GPU.
#
# The snapshot is fetched over plain HTTP because the base image carries no
# certificate store; apt verifies every package against the archive's signed
# index, and the date makes that index fixed.
FROM debian:trixie@sha256:4c25275446ca371766b8b093c696782eabce06263fccacb8d5d813b3dc44b308

ARG SNAPSHOT=20260926T000000Z
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* \
 && printf '%s\n' \
    "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/${SNAPSHOT} trixie main" \
    "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/${SNAPSHOT} trixie-updates main" \
    "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/${SNAPSHOT} trixie-security main" \
    > /etc/apt/sources.list \
 && printf '%s\n' "Package: *" "Pin: origin snapshot.debian.org" "Pin-Priority: 1001" \
    > /etc/apt/preferences.d/snapshot \
 && apt-get -o Acquire::Retries=5 update \
 && DEBIAN_FRONTEND=noninteractive apt-get -y --allow-downgrades dist-upgrade \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      gcc libc6-dev make python3 \
      libsdl2-dev libopenal-dev \
      libgl1 libegl1 libgbm1 libgl1-mesa-dri \
 && rm -rf /var/lib/apt/lists/*

# What this environment is, for build records.
RUN { echo "snapshot=${SNAPSHOT}"; \
      echo "gcc=$(gcc -dumpfullversion)"; \
      for p in libc6 libsdl2-2.0-0 libopenal1 libgl1 libasound2t64 libstdc++6; do \
        echo "$p=$(dpkg-query -W -f='${Version}' $p 2>/dev/null)"; done; } > /etc/mun-devkit-env
