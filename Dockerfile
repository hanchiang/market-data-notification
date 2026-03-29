# Dockerfile

# pull the official docker image
FROM python:3.12-slim-bullseye as base

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
ENV VIRTUAL_ENV /app/.venv
ENV POETRY_VIRTUALENVS_IN_PROJECT true
ENV PATH "${VIRTUAL_ENV}/bin:${PATH}:/root/.local/bin"
ENV PYTHONPATH "${PYTHONPATH}:$(pwd)"

COPY pyproject.toml poetry.lock ./

# Install dependencies. Retry apt to reduce transient mirror/network failures in CI.
RUN set -eux; \
    for attempt in 1 2 3; do \
        if apt-get update -o Acquire::Retries=5 && \
           apt-get install -y --no-install-recommends --fix-missing curl git wget unzip gnupg xz-utils build-essential; then \
            break; \
        fi; \
        if [ "$attempt" -eq 3 ]; then exit 1; fi; \
        sleep 5; \
        rm -rf /var/lib/apt/lists/*; \
    done; \
    curl -sSL https://install.python-poetry.org | python3 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# The backend image intentionally does not install Chrome. Dockerized runs use
# a separate remote Selenium container instead of launching a local browser here.
# set up GitHub authentication for the private market data library
RUN --mount=type=secret,id=github_token git config --global \
    url."https://x-access-token:$(cat /run/secrets/github_token)@github.com/".insteadOf \
    ssh://git@github.com/ \
&& poetry install --no-root \
&& rm -f /root/.gitconfig

COPY . .

RUN rm -rf "$(pwd)/secret"

FROM base as dev
CMD ["python3", "main.py"]

FROM base as test
CMD ["pytest"]

FROM base AS release
COPY --from=base . .
RUN --mount=type=secret,id=github_token rm -rf "$VIRTUAL_ENV" \
&& git config --global \
    url."https://x-access-token:$(cat /run/secrets/github_token)@github.com/".insteadOf \
    ssh://git@github.com/ \
&& poetry install --only main --no-root \
&& rm -f /root/.gitconfig
CMD ["python3", "main.py"]
