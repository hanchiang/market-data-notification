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

COPY secret/id_rsa /root/.ssh/id_rsa
RUN chmod 600 /root/.ssh/id_rsa

#ARG BARCHART_API_TAG
#RUN if [ -z "$BARCHART_API_TAG" ]; then echo "BARCHART_API_TAG is required"; exit 1; fi
#RUN poetry add git+ssh://git@github.com/hanchiang/market_data_api.git@$BARCHART_API_TAG && rm /root/.ssh/id_rsa

COPY . .

RUN rm -rf "$(pwd)/secret"
RUN poetry env use python3.10 && . $(poetry env info --path)/bin/activate

FROM base as dev
RUN poetry install
CMD ["poetry", "run", "python3", "main.py"]

FROM base as test
RUN poetry install
CMD ["poetry", "run", "pytest"]

FROM base AS release
COPY --from=base . .
RUN rm -rf $(poetry env info --path) && poetry install --only main
CMD ["poetry", "run", "python3", "main.py"]