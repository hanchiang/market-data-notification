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
RUN apt update && apt install -y curl git && curl -sSL https://install.python-poetry.org | python3

COPY . .

RUN poetry env use python3.9 && . $(poetry env info --path)/bin/activate

FROM base as dev
RUN poetry install
CMD ["poetry", "run", "python3", "main.py"]

FROM base as test
CMD ["poetry", "run", "pytest"]

FROM base AS release
COPY --from=base . .
RUN rm -rf $(poetry env info --path)/lib/python3.9/site-packages && poetry install --no-dev
CMD ["poetry", "run", "python3", "main.py"]