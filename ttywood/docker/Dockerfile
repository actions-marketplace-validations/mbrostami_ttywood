# ttywood episode container: everything a CLI tool might need to
# build and run, plus zellij + asciinema for the on-camera terminal and
# Claude Code for the rehearsal pass.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    TERM=xterm-256color \
    LANG=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget ca-certificates build-essential pkg-config cmake \
    python3 python3-pip python3-venv pipx \
    asciinema jq ripgrep unzip file locales sudo \
    tmux \
    && rm -rf /var/lib/apt/lists/*

# Docker engine, for docker-first projects (n8n, appwrite, gitea...): only
# usable when the episode container runs --privileged; dockerd is started by
# the pipeline (rehearse.start_dockerd) with fuse-overlayfs storage
RUN apt-get update && apt-get install -y --no-install-recommends \
    docker.io docker-compose-v2 fuse-overlayfs iptables \
    && rm -rf /var/lib/apt/lists/*

# Node (for npm-installable tools and for Claude Code itself)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

# Go
RUN ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://go.dev/dl/go1.24.5.linux-${ARCH}.tar.gz" | tar -C /usr/local -xz
ENV PATH="/usr/local/go/bin:${PATH}"

# demo user: episodes run as a normal user with a nice prompt, not root
RUN useradd -m -s /bin/bash demo && echo "demo ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/demo \
    && usermod -aG docker demo
USER demo
WORKDIR /home/demo

# Rust (per-user, gives cargo install for the many Rust CLIs)
RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal
ENV PATH="/home/demo/.cargo/bin:/home/demo/.local/bin:/home/demo/go/bin:${PATH}"

# zellij (the on-camera multiplexer)
RUN ARCH=$(uname -m) \
    && curl -fsSL "https://github.com/zellij-org/zellij/releases/latest/download/zellij-${ARCH}-unknown-linux-musl.tar.gz" \
       | tar -xz -C /home/demo/.local/bin 2>/dev/null \
    || (mkdir -p /home/demo/.local/bin && curl -fsSL "https://github.com/zellij-org/zellij/releases/latest/download/zellij-${ARCH}-unknown-linux-musl.tar.gz" | tar -xz -C /home/demo/.local/bin)

# Claude Code CLI for the rehearsal pass
RUN sudo npm install -g @anthropic-ai/claude-code

# Playwright + Chromium for web-app episodes (headless, records its own video)
RUN pipx install playwright && playwright install --with-deps chromium 2>/dev/null \
    || (sudo /home/demo/.local/share/pipx/venvs/playwright/bin/playwright install-deps chromium \
        && playwright install chromium)
ENV PLAYWRIGHT_BROWSERS_PATH=/home/demo/.cache/ms-playwright
COPY --chown=demo:demo container/webdriver.py /opt/ttywood/webdriver.py

# clean, video-friendly shell prompt
RUN printf '%s\n' \
      'export PS1="\\[\\e[1;36m\\]demo\\[\\e[0m\\] \\[\\e[1;34m\\]\\w\\[\\e[0m\\] $ "' \
      'export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$HOME/go/bin:/usr/local/go/bin:$PATH"' \
      >> /home/demo/.bashrc

# zellij config: hide the help hints bar for a cleaner recording
RUN mkdir -p /home/demo/.config/zellij && printf '%s\n' \
      'simplified_ui true' \
      'pane_frames false' \
      'default_layout "compact"' \
      'show_startup_tips false' \
      'show_release_notes false' \
      'default_shell "bash"' \
      > /home/demo/.config/zellij/config.kdl

ENV SHELL=/bin/bash
CMD ["/bin/bash"]
