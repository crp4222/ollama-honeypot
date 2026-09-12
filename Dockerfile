FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin lab \
    && mkdir /data && chown 10001:10001 /data
COPY honeypot ./honeypot
COPY config/system.txt ./config/system.txt
USER 10001:10001
CMD ["uvicorn", "honeypot.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-proxy-headers", "--no-access-log", "--no-server-header", "--limit-concurrency", "32", "--timeout-keep-alive", "5"]
