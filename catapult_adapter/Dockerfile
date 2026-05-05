# Catapult adapter image. Builds the same code tree as the main app but
# starts ONLY the adapter's FastAPI service (catapult_adapter.service.main:app).
#
# Build context expected: the repository root.
#   docker build -f catapult_adapter/Dockerfile -t rag-chatbot-catapult:1.0.0 .
#
# Listens on 8080 — the value the Catapult Runtime expects in the tool's
# tool_config.service_url.

FROM python:3.11-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn uvicorn

COPY app ./app
COPY catapult_adapter ./catapult_adapter

ENV PYTHONPATH=/srv
EXPOSE 8080

# Health check matches the manifest's status endpoint. Catapult's own
# orchestrator will also probe /health/ready and /health/live.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://localhost:8080/tools/rag-chatbot/health', timeout=3).status==200 else 1)" \
    || exit 1

CMD ["gunicorn", "catapult_adapter.service.main:app", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-w", "2", \
     "-b", "0.0.0.0:8080", \
     "--timeout", "60"]
