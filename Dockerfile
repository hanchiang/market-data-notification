# Dockerfile

# pull the official docker image
FROM python:3.10-slim-bullseye as base

# set work directory
WORKDIR /app

EXPOSE 8080

# set env variables
ENV PYTHONUNBUFFERED 1
ENV PATH "${PATH}:/root/.local/bin"
ENV PYTHONPATH "${PYTHONPATH}:$(pwd)"

COPY pyproject.toml poetry.lock ./

# install dependencies
RUN apt update && apt install -y curl git && curl -sSL https://install.python-poetry.org | python3

# set up ssh, install market data library
RUN mkdir -p /root/.ssh
RUN ssh-keyscan github.com >> /root/.ssh/known_hosts

ARG MARKET_DATA_API_TAG
RUN if [ -z "$MARKET_DATA_API_TAG" ]; then echo "MARKET_DATA_API_TAG is required"; exit 1; fi

#ARG REPO_SSH_PRIVATE_KEY
#RUN if [ -d "secret" ]; then echo "secret exist"; else echo "secret does not exist"; fi
#RUN if [ -d "secret" ]; then COPY secret/id_rsa /root/.ssh/id_rsa; else echo $REPO_SSH_PRIVATE_KEY > /root/.ssh/id_rsa; fi
#RUN ls -la && ls -la /root/.ssh && cat /root/.ssh/id_rsa


#RUN echo $REPO_SSH_PRIVATE_KEY > /root/.ssh/id_rsa

RUN poetry env use python3.10 && . $(poetry env info --path)/bin/activate

RUN --mount=type=secret,id=ssh_private_key,target=/root/.ssh/id_rsa poetry install

# When run locally
#RUN if [ -d "secret" ]; then COPY secret/id_rsa /root/.ssh/id_rsa; chmod 600 /root/.ssh/id_rsa; fi
#COPY secret/id_rsa /root/.ssh/id_rsa
#RUN chmod 600 /root/.ssh/id_rsa;
# RUN poetry add git+ssh://git@github.com/hanchiang/market_data_api.git@$MARKET_DATA_API_TAG
# When run in CI pipeline
#RUN poetry add git+ssh://git@github.com/hanchiang/market_data_api.git@$MARKET_DATA_API_TAG;

RUN poetry install

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