FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    PORT=8000
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app app && mkdir /data && chown app:app /data
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chmod=0644 main.py gateway.py storage.py ./
EXPOSE 8000
CMD ["sh", "-c", "chown app:app /data && exec setpriv --reuid=10001 --regid=10001 --clear-groups uvicorn main:app --host 0.0.0.0 --port \"$PORT\" --workers 1 --log-level warning"]
