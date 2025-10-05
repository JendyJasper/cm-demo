FROM python:3.11-slim as builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

FROM python:3.11-slim

RUN groupadd -r app && useradd -r -g app app
WORKDIR /app

COPY --from=builder /root/.local /home/app/.local
COPY user_service/ .

ENV PATH="/home/app/.local/bin:$PATH"

USER app
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]