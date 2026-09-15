# Builds the interviewr .deb on Ubuntu 24.04 so it installs on 24.04 and newer.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
# Install path for the engine payload (not PREFIX: setup.py passes env vars to cmake).
ENV INTERVIEWR_PREFIX=/usr/lib/interviewr

# Build tools, Vulkan headers and the webkit/gtk libraries Tauri links against.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl xz-utils file patchelf \
      build-essential pkg-config \
      cmake glslc glslang-tools libvulkan-dev spirv-headers \
      libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev libayatana-appindicator3-dev \
      libsoup-3.0-dev libjavascriptcoregtk-4.1-dev \
      dpkg-dev fakeroot \
    && rm -rf /var/lib/apt/lists/*

# Bundled standalone Python, so the package doesn't depend on the distro's Python.
ARG PYTHON_VERSION=3.12.8
ARG PBS_RELEASE=20241219
RUN curl -fsSL -o /tmp/python.tar.gz \
      "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/cpython-${PYTHON_VERSION}+${PBS_RELEASE}-x86_64-unknown-linux-gnu-install_only.tar.gz" \
    && mkdir -p "$INTERVIEWR_PREFIX" \
    && tar -xzf /tmp/python.tar.gz -C "$INTERVIEWR_PREFIX" \
    && rm /tmp/python.tar.gz \
    && "$INTERVIEWR_PREFIX/python/bin/python3" -V

# Pinned Rust toolchain for building the overlay.
ARG RUST_VERSION=1.91.1
RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --default-toolchain "$RUST_VERSION" --profile minimal
ENV PATH=/root/.cargo/bin:$PATH

# Pinned Node for building the overlay's frontend.
ARG NODE_VERSION=22.12.0
RUN curl -fsSL -o /tmp/node.tar.xz \
      "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
    && rm /tmp/node.tar.xz \
    && node --version && npm --version

WORKDIR /src
COPY . /src

# Parallel compile jobs; lower it if the build runs out of memory.
ARG BUILD_JOBS=8
ENV CMAKE_BUILD_PARALLEL_LEVEL=${BUILD_JOBS}
ENV MAKEFLAGS=-j${BUILD_JOBS}

# Installs the engine's dependencies and compiles pywhispercpp with Vulkan into the bundled Python.
# GGML_NATIVE=OFF builds for any x86-64 CPU with AVX2 instead of the build machine's own CPU.
RUN INTERVIEWR_VENV="$INTERVIEWR_PREFIX/python" INTERVIEWR_CMAKE_ARGS="-DGGML_NATIVE=OFF" \
    ./scripts/install.sh --build-only

# Removes dev-only packages, the editable install, caches and bundled test folders.
RUN SITE=$("$INTERVIEWR_PREFIX/python/bin/python3" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])') \
    && "$INTERVIEWR_PREFIX/python/bin/python3" -m pip uninstall -y interviewr-ai ruff mypy pytest pytest-asyncio jiwer 2>/dev/null || true \
    && find "$SITE" -name '__pycache__' -prune -exec rm -rf {} + \
    && find "$SITE" -name 'tests' -maxdepth 2 -type d -prune -exec rm -rf {} + \
    && echo "payload site-packages: $(du -sh "$SITE" | cut -f1)"

# Keeps only the library names the engine loads (each soname), since the bundler copies every alias in full.
RUN SITE=$("$INTERVIEWR_PREFIX/python/bin/python3" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])') \
    && cd "$SITE" \
    && for so in libggml*.so* libwhisper*.so*; do \
         soname=$(patchelf --print-soname "$so") || continue; \
         if [ "$so" != "$soname" ] && [ -e "$soname" ]; then rm "$so"; fi; \
       done \
    && "$INTERVIEWR_PREFIX/python/bin/python3" -c 'import _pywhispercpp' \
    && { ! objdump -d libggml-cpu.so.0 | grep -q zmm || { echo "libggml-cpu uses AVX-512; the build is not generic" >&2; exit 1; }; } \
    && ls libggml*.so* libwhisper*.so* \
    && echo "after pruning: $(du -sh "$SITE" | cut -f1)"

# Copies the engine source into the payload.
RUN mkdir -p "$INTERVIEWR_PREFIX/backend" \
    && cp -a /src/backend/app /src/backend/static /src/backend/run_pipeline.py "$INTERVIEWR_PREFIX/backend/" \
    && find "$INTERVIEWR_PREFIX/backend" -name '__pycache__' -prune -exec rm -rf {} +

# Builds the overlay and the .deb; --locked fails the build if Cargo.lock is out of date.
RUN cd /src/overlay \
    && npm ci \
    && npm run tauri build -- --bundles deb -- --locked

# Collects the .deb for scripts/build-deb.sh.
RUN mkdir -p /out && cp /src/overlay/src-tauri/target/release/bundle/deb/*.deb /out/
