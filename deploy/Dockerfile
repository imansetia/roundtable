# Roundtable in Docker (M10). Shape A in a container — see
# docs/ADD-AGENT.md. Build from the repo root:
#   docker build -t roundtable -f deploy/Dockerfile .
# Run (the board stays on this machine only):
#   docker run --rm -p 127.0.0.1:8000:8000 \
#       -v roundtable-data:/data \
#       -v "$PWD/agents.json:/config/agents.json:ro" \
#       roundtable
# The -p flag publishes the port on the HOST's loopback only, so
# the board is never exposed to the network. Inside the container
# the app binds 0.0.0.0 because Docker's port mapping targets the
# container's interface (a 127.0.0.1 bind inside the container
# would refuse it); the loopback protection lives at the publish
# step. HTTPS/nginx stays a shape-B concern.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY roundtable/ ./roundtable/
RUN pip install --no-cache-dir .
# The database lives in a volume so it survives container restarts.
VOLUME /data
ENV ROUNDTABLE_HOST=0.0.0.0
ENV ROUNDTABLE_DB_PATH=/data/roundtable.db
ENV ROUNDTABLE_AGENTS_CONFIG=/config/agents.json
EXPOSE 8000
CMD ["python", "-m", "roundtable"]
