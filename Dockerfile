# ⚡ NornPulse — container image (Cloud Run target)
# Norn Labs (nornlabs.ai)
#
# Build:  docker build -t nornpulse .
# Run:    docker run -p 8080:8080 --env-file .env nornpulse
#
# Three things in here are load-bearing and easy to get wrong; see the
# comments at each step for why:
#   1. ffmpeg      — Skuld shells out to it for every render (and ffprobe
#                    for duration/fps/volume probing). No ffmpeg, no clips.
#   2. fonts       — burned-in captions name a real display font. Without
#                    fonts installed, libass SILENTLY substitutes, so the
#                    deployed output looks different from local with no
#                    error anywhere.
#   3. mcp-clickhouse — must land in the SAME environment that runs the
#                    app, since Urðr launches it as a subprocess resolved
#                    relative to sys.executable.

FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg/ffprobe for Skuld's rendering and probing.
#
# Fonts matter more than they look. Burned-in captions name a heavy
# display weight, and libass substitutes SILENTLY when it can't find
# one. Measured on this image before fonts-roboto-unhinted was added:
# `fc-match "Arial Black"` returned `DejaVu Sans "Book"` -- regular
# weight -- so captions rendered visibly lighter than on a dev box with
# MS core fonts, with nothing logged. fonts-roboto-unhinted ships a
# genuine Roboto Black, which CAPTION_FONT selects below.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fontconfig \
        fonts-dejavu-core \
        fonts-liberation \
        fonts-roboto-unhinted \
        fonts-league-spartan \
        fonts-lato \
        fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f \
    && fc-match "Roboto Black" | grep -qi "black" \
       || (echo "FATAL: no black-weight caption font resolved in image" && exit 1) \
    # Noto Color Emoji is installed for glyph coverage, but note that
    # libass cannot render colour bitmap emoji (CBDT/sbix): burning an
    # emoji into a caption produces monochrome or tofu, measured on this
    # image at zero non-grey pixels. Emoji belong in the YouTube title and
    # description, which render them properly; burning them into the frame
    # would need an ffmpeg PNG overlay instead.
    && fc-list | grep -qi "NotoColorEmoji" \
       || (echo "FATAL: no emoji font in image" && exit 1)

# Skuld reads this; see agent/skuld_renderer.CAPTION_FONT. The local
# default is "Arial Black", which does not exist here.
ENV CAPTION_FONT="Roboto Black"

WORKDIR /app

# Dependencies first, so application edits don't bust the pip layer cache.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Fail the BUILD, not a silent runtime fallback, if the ClickHouse MCP
# server didn't make it into this image. Urðr degrades to in-memory
# benchmarks when it can't reach ClickHouse, which means a broken image
# would deploy and serve synthetic data while looking perfectly healthy.
# Catching that here is far cheaper than catching it in a live demo.
RUN python -c "\
import sys, pathlib, shutil; \
p = pathlib.Path(sys.executable).parent / 'mcp-clickhouse'; \
found = str(p) if p.is_file() else shutil.which('mcp-clickhouse'); \
print('mcp-clickhouse ->', found); \
sys.exit(0 if found else 'FATAL: mcp-clickhouse missing from the image')"

COPY . .

# Cloud Run mounts a writable in-memory layer, but these are created up
# front so a first run never races on mkdir.
RUN mkdir -p output_clips sample_data

# Cloud Run injects $PORT (8080 by default) and expects the container to
# listen on it; the default is set so plain `docker run` works too.
ENV PORT=8080
EXPOSE 8080

# Shell form so $PORT expands at runtime rather than being baked in.
# address=0.0.0.0 is required for Cloud Run to reach the process at all.
# XSRF protection is left ON; CORS is disabled because Cloud Run
# terminates TLS upstream and Streamlit's CORS check misreads the origin.
CMD streamlit run app.py \
        --server.port=$PORT \
        --server.address=0.0.0.0 \
        --server.headless=true \
        --server.enableCORS=false \
        --browser.gatherUsageStats=false
