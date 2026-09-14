# Build the interviewr .deb.
#
# The base is deliberately OLD. glibc symbols are forward-compatible but not backward: anything
# compiled against glibc 2.43 (Ubuntu 26.04) refuses to load on 2.39 (24.04), and in this project
# it only takes one symbol, `sqrtf`, which glibc 2.43 re-versioned. Building on 24.04 therefore
# reaches 24.04 through 26.04+, Debian 13, Mint 22+ and Pop, from a single artifact. Building on
# the maintainer's own machine would reach 26.04 and nothing else.
#
# Both halves are built here for that reason, not just the engine: the Tauri binary has the same
# floor, and libwebkit2gtk-4.1 is the oldest webkit the overlay can link against.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
# Where the package will live on the user's machine. The payload is assembled under this exact path
# so nothing has to be rewritten afterwards.
ENV PREFIX=/usr/lib/interviewr

# (Build parallelism is declared further down, just above the compile that uses it, so tuning it
# does not invalidate the expensive apt layer.)

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl xz-utils file patchelf \
      build-essential pkg-config \
      cmake glslc glslang-tools libvulkan-dev spirv-headers \
      libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev libayatana-appindicator3-dev \
      libsoup-3.0-dev libjavascriptcoregtk-4.1-dev \
      dpkg-dev fakeroot \
    && rm -rf /var/lib/apt/lists/*

# Bundled interpreter, rather than a dependency on the distro's.
#
# There is no single system Python to depend on across the target range: 24.04 ships 3.12, 26.04
# ships only 3.14 and carries no python3.12 at all. A python-build-standalone tree is relocatable
# and self-contained, so one artifact serves every release and the extension ABI is fixed at build
# time by construction.
ARG PYTHON_VERSION=3.12.8
ARG PBS_RELEASE=20241219
RUN curl -fsSL -o /tmp/python.tar.gz \
      "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/cpython-${PYTHON_VERSION}+${PBS_RELEASE}-x86_64-unknown-linux-gnu-install_only.tar.gz" \
    && mkdir -p "$PREFIX" \
    && tar -xzf /tmp/python.tar.gz -C "$PREFIX" \
    && rm /tmp/python.tar.gz \
    && "$PREFIX/python/bin/python3" -V

# Rust and node for the overlay. Pinned rather than "latest" so a rebuild six months from now is
# the same build.
ARG RUST_VERSION=1.83.0
RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --default-toolchain "$RUST_VERSION" --profile minimal
ENV PATH=/root/.cargo/bin:$PATH

ARG NODE_VERSION=22.12.0
RUN curl -fsSL -o /tmp/node.tar.xz \
      "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
    && rm /tmp/node.tar.xz \
    && node --version && npm --version

WORKDIR /src
COPY . /src

# Cap build parallelism. ggml's Vulkan backend compiles thousands of SPIR-V shader variants, and at
# one job per core that is the peak memory moment of the whole build: enough to get the builder
# OOM-killed on a 16-core machine with other things running. It is also the single longest stage, so
# this is the main speed/memory dial. Declared here rather than at the top of the file so changing it
# reuses the cached apt layer instead of reinstalling 463 packages.
ARG BUILD_JOBS=8
ENV CMAKE_BUILD_PARALLEL_LEVEL=${BUILD_JOBS}
ENV MAKEFLAGS=-j${BUILD_JOBS}

# The engine payload. INTERVIEWR_VENV points install.sh at the bundled interpreter instead of
# creating a venv: the directory already exists, so its `python3 -m venv` step is skipped and
# everything lands in the standalone tree's own site-packages.
#
# --build-only skips the checks that need a GPU present. Compiling against the Vulkan headers does
# not need a device, but a container has no /dev/dri, so the runtime probe and the ggml smoke test
# would both reject a perfectly good build. Which device to use is a per-machine choice the app
# makes on first run anyway.
# Currently fails at configure with ggml_get_flags; see packaging/STATUS.md.
RUN INTERVIEWR_VENV="$PREFIX/python" ./scripts/install.sh --build-only

# Strip what only a developer needs. The cloud SDKs stay: the wizard offers cloud as a first-class
# path, so a package without them would be half an app.
#
# interviewr-ai itself goes too. install.sh installs it with `pip install -e`, which leaves a .pth
# pointing at /src/backend: correct in this container, a dangling path on the user's machine.
# Uninstalling drops the pointer and keeps the dependencies, and the engine is run from the shipped
# source below anyway, which Python puts ahead of site-packages.
RUN SITE=$("$PREFIX/python/bin/python3" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])') \
    && "$PREFIX/python/bin/python3" -m pip uninstall -y interviewr-ai ruff mypy pytest pytest-asyncio jiwer 2>/dev/null || true \
    && find "$SITE" -name '__pycache__' -prune -exec rm -rf {} + \
    && find "$SITE" -name 'tests' -maxdepth 2 -type d -prune -exec rm -rf {} + \
    && echo "payload site-packages: $(du -sh "$SITE" | cut -f1)"

# Deduplicate the ggml libraries. patchelf rewrites each file in place, which breaks the hardlinks
# the wheel shipped, leaving three independent 57 MB copies of libggml-vulkan. Re-linking the
# versioned names as symlinks is worth ~115 MB, a quarter of the package.
RUN SITE=$("$PREFIX/python/bin/python3" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])') \
    && cd "$SITE" \
    && for base in libggml-vulkan libggml-base libggml-cpu libggml libwhisper; do \
         canonical=$(ls ${base}.so.*.* 2>/dev/null | head -1) || continue; \
         [ -n "$canonical" ] || continue; \
         for dup in ${base}.so ${base}.so.[0-9]*; do \
           [ "$dup" = "$canonical" ] && continue; \
           [ -e "$dup" ] || continue; \
           cmp -s "$dup" "$canonical" && ln -sf "$canonical" "$dup"; \
         done; \
       done \
    && echo "after dedupe: $(du -sh "$SITE" | cut -f1)"

# The engine source, which the overlay runs with the bundled interpreter. Shipped as source rather
# than relying on the site-packages copy so `backend/static/` comes along: it lives outside `app/`
# and pyproject's wheel only packages ["app"].
RUN mkdir -p "$PREFIX/backend" \
    && cp -a /src/backend/app /src/backend/static /src/backend/run_pipeline.py "$PREFIX/backend/" \
    && find "$PREFIX/backend" -name '__pycache__' -prune -exec rm -rf {} +

# The overlay, and the .deb itself. tauri.conf.json's deb.files pulls $PREFIX in.
RUN cd /src/overlay \
    && npm ci \
    && npm run tauri build -- --bundles deb

# Collected by scripts/build-deb.sh.
RUN mkdir -p /out && cp /src/overlay/src-tauri/target/release/bundle/deb/*.deb /out/
