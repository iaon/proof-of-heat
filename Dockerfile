FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/app --shell /bin/sh app \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY VERSION ./
COPY proof_of_heat ./proof_of_heat
COPY docs ./docs
COPY README.md ./

RUN mkdir -p /app/data \
    && chown -R app:app /app /home/app

USER 1000:1000

EXPOSE 8000

CMD ["python", "-m", "proof_of_heat.main"]
