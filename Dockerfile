FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data

ENV PORT=8000
EXPOSE 8000

RUN python3 -m data.seed || true

CMD ["python3", "-m", "app.main"]
