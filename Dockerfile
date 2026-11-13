FROM python:3.11-slim AS dependencies

WORKDIR /build

RUN rm -rf /var/lib/apt/lists/* \
    && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/app

WORKDIR /app

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/app --shell /bin/sh app \
    && mkdir -p /app/data \
    && chown app:app /app /app/data

COPY --from=dependencies /install/ /usr/local/

COPY --chown=app:app requirements.txt VERSION README.md ./
COPY --chown=app:app proof_of_heat ./proof_of_heat
COPY --chown=app:app docs ./docs

USER 1000:1000

EXPOSE 8000

CMD ["python", "-m", "proof_of_heat.main"]
