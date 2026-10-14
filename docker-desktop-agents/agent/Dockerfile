# Full Linux desktop + VNC + noVNC, bundled by the base image's supervisord.
FROM dorowu/ubuntu-desktop-lxde-vnc:latest

# Model baked in at build time: once pulled, the container needs zero
# internet access at runtime — agent.py only ever calls its own 127.0.0.1.
ARG OLLAMA_MODEL=moondream
ENV OLLAMA_MODEL=${OLLAMA_MODEL}

# The base image ships a Google Chrome apt source we don't need, whose
# signing key is unavailable here and fails `apt-get update` outright.
RUN find /etc/apt/sources.list.d/ -name '*.list' -exec grep -l 'dl.google.com' {} \; | xargs -r rm -f

RUN apt-get update && apt-get install -y --no-install-recommends \
        xdotool \
        imagemagick \
        python3 \
        curl \
        ca-certificates \
        zstd \
    && rm -rf /var/lib/apt/lists/*

# Some ImageMagick builds ship a policy that blocks reading the root window;
# the computer-use loop needs `import -window root` to work.
RUN sed -i 's/rights="none" pattern="@\*"/rights="read|write" pattern="@*"/' \
        /etc/ImageMagick-6/policy.xml 2>/dev/null || true

RUN curl -fsSL https://ollama.com/install.sh | sh

# Start the server just long enough to pull the model into the image layer,
# then stop it — supervisord starts the real long-running instance at runtime.
RUN (ollama serve &) \
    && for i in $(seq 1 30); do curl -sf http://127.0.0.1:11434/api/tags >/dev/null && break; sleep 1; done \
    && ollama pull "${OLLAMA_MODEL}" \
    && (pkill ollama || true) \
    && sleep 1

WORKDIR /opt/agent
COPY agent.py .
COPY public ./public

COPY supervisor-agent.conf /etc/supervisor/conf.d/agent.conf

# Base image's own entrypoint starts Xvfb/x11vnc/noVNC/supervisord; our
# ollama server, agent loop, and the live-view HTTP server (port 7000:
# latest screenshot + reasoning, see public/index.html) are three more
# supervisor-managed processes on top of that.
