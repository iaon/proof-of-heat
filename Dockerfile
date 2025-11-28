FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY proof_of_heat ./proof_of_heat
COPY docs ./docs
COPY README.md ./

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "proof_of_heat.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
