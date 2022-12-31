# Dockerfile

# pull the official docker image
FROM python:3.9.8-slim as base

# set work directory
WORKDIR /app

EXPOSE 8080

# set env variables
ENV PYTHONUNBUFFERED 1
ENV PATH "${PATH}:/root/.local/bin"
ENV PYTHONPATH "${PYTHONPATH}:$(pwd)"

COPY pyproject.toml poetry.lock ./

# install dependencies
RUN apt update && apt install -y build-essential curl git \
    && curl -sSL https://install.python-poetry.org | python3 - \
    && poetry install

COPY . .

FROM base as dev
CMD ["poetry", "run", "python3", "main.py"]