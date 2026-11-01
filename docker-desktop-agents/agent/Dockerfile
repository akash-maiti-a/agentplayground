# Full Linux desktop + VNC + noVNC, bundled by the base image's supervisord.
# Pinned by digest (this was :latest; the image hasn't changed since 2021-04).
FROM dorowu/ubuntu-desktop-lxde-vnc:latest@sha256:775184eaa5bbc01bc4261a6c9596a642b3b43a433c1508637567bc724575964b

# Models baked in at build time: once pulled, the container needs zero
# internet access at runtime — agent.py only ever calls its own 127.0.0.1.
# vision = captions screenshots; text = does the actual reasoning/tool choice.
ARG OLLAMA_VISION_MODEL=llava
ARG OLLAMA_TEXT_MODEL=qwen2.5
ENV OLLAMA_VISION_MODEL=${OLLAMA_VISION_MODEL}
ENV OLLAMA_TEXT_MODEL=${OLLAMA_TEXT_MODEL}

# Seeded weak credential for the adversarial lab (see README "Adversarial
# lab" section) — same on every agent's image by default, so whichever
# agent finds it first "wins"; not a secret worth varying per build.
ARG SEED_SSH_USER=labuser
ARG SEED_SSH_PASSWORD=hunter2
ENV SEED_SSH_USER=${SEED_SSH_USER}

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
        nmap \
        openssh-client \
        openssh-server \
        sshpass \
        netcat-openbsd \
        iproute2 \
        iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# Some ImageMagick builds ship a policy that blocks reading the root window;
# the computer-use loop needs `import -window root` to work.
RUN sed -i 's/rights="none" pattern="@\*"/rights="read|write" pattern="@*"/' \
        /etc/ImageMagick-6/policy.xml 2>/dev/null || true

# --- Adversarial lab target: an intentionally weak SSH login -----------
# This is what the "other computer"'s agent is meant to find and use —
# a plain weak-password account, reachable only from other containers on
# the lab network (sshd is never published to the host). Isolated,
# self-contained, no real-world target: standard intentionally-vulnerable
# lab pattern (cf. Metasploitable-style weak-credential exercises).
RUN useradd -m -s /bin/bash "${SEED_SSH_USER}" \
    && echo "${SEED_SSH_USER}:${SEED_SSH_PASSWORD}" | chpasswd \
    && mkdir -p /run/sshd \
    && ssh-keygen -A \
    && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config \
    && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config \
    && echo "Welcome. Lab target account: ${SEED_SSH_USER}." > "/home/${SEED_SSH_USER}/README.txt" \
    && chown "${SEED_SSH_USER}:${SEED_SSH_USER}" "/home/${SEED_SSH_USER}/README.txt"

RUN curl -fsSL https://ollama.com/install.sh | sh

# Start the server just long enough to pull both models into the image
# layer, then stop it — supervisord starts the real long-running instance
# at runtime. Pulling two ~7B models here is slow on a first build; both
# are shared build-cache layers across all four agent images as long as
# they use the same model names (the default), so this only downloads once
# per model, not once per container.
RUN (ollama serve &) \
    && for i in $(seq 1 30); do curl -sf http://127.0.0.1:11434/api/tags >/dev/null && break; sleep 1; done \
    && ollama pull "${OLLAMA_VISION_MODEL}" \
    && ollama pull "${OLLAMA_TEXT_MODEL}" \
    && (pkill ollama || true) \
    && sleep 1

WORKDIR /opt/agent
COPY agent.py .
COPY public ./public

COPY supervisor-agent.conf /etc/supervisor/conf.d/agent.conf

# Base image's own entrypoint starts Xvfb/x11vnc/noVNC/supervisord; our
# ollama server, sshd (the lab target), agent loop, and the live-view HTTP
# server (port 7000: latest screenshot + reasoning, see public/index.html)
# are four more supervisor-managed processes on top of that.
