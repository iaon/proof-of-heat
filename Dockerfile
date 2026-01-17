FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY proof_of_heat ./proof_of_heat
COPY docs ./docs
COPY README.md ./

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["python", "-m", "proof_of_heat.main"]
