FROM python:3.11-slim as builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /root/.local /usr/local
COPY user_service/ .

RUN groupadd -r app && useradd -r -g app app
RUN chmod -R a+r /usr/local && find /usr/local -type d -exec chmod a+x {} \;

USER app
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]