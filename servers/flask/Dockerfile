# Build context: servers/
FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app/flask
COPY flask/requirements.txt ./
RUN pip install -r requirements.txt
COPY openapi.json /app/openapi.json
COPY flask/ ./
USER nobody
ENV PORT=3000
EXPOSE 3000
CMD ["gunicorn"]
