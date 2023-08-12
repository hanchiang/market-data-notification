# Dockerfile

# pull the official docker image
FROM python:3.10-slim-bullseye as base

ARG TARGETPLATFORM
ARG TARGETARCH
ARG TARGETVARIANT
RUN printf "I'm building for TARGETPLATFORM=${TARGETPLATFORM}" \
    && printf ", TARGETARCH=${TARGETARCH}" \
    && printf ", TARGETVARIANT=${TARGETVARIANT} \n" \
    && printf "With uname -s : " && uname -s \
    && printf "and  uname -m : " && uname -m

# set work directory
WORKDIR /app

EXPOSE 8080

# set env variables
ENV PYTHONUNBUFFERED 1
ENV PATH "${PATH}:/root/.local/bin"
ENV PYTHONPATH "${PYTHONPATH}:$(pwd)"

COPY pyproject.toml poetry.lock ./

# Install dependencies
RUN apt update -y && apt install -y curl git wget unzip gnupg xz-utils && curl -sSL https://install.python-poetry.org | python3

# Install google chrome
RUN if [ "$TARGETPLATFORM" = "linux/amd64" ]; then \
    wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - && \
    echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list && \
    apt update -y && apt install -y google-chrome-stable; \
elif [ "$TARGETPLATFORM" = "linux/arm64" ]; then \
    wget https://launchpad.net/ubuntu/+archive/primary/+sourcefiles/chromium-browser/1:85.0.4183.83-0ubuntu2.22.04.1/chromium-browser_85.0.4183.83-0ubuntu2.22.04.1.tar.xz && \
    tar -xvf chromium-browser_85.0.4183.83-0ubuntu2.22.04.1.tar.xz && \
    cp chromium-browser-85.0.4183.83/chromedriver /usr/local/bin && \
    cp chromium-browser-85.0.4183.83/chromium-browser /usr/local/bin; \
fi

# set up ssh for market data library
RUN mkdir -p /root/.ssh
RUN ssh-keyscan github.com >> /root/.ssh/known_hosts

RUN poetry env use python3.10 && . $(poetry env info --path)/bin/activate

RUN --mount=type=secret,id=ssh_private_key,target=/root/.ssh/id_rsa poetry install

COPY . .

RUN rm -rf "$(pwd)/secret"

FROM base as dev
CMD ["poetry", "run", "python3", "main.py"]

FROM base as test
CMD ["poetry", "run", "pytest"]

FROM base AS release
COPY --from=base . .
RUN rm -rf $(poetry env info --path) && poetry install --only main
CMD ["poetry", "run", "python3", "main.py"]