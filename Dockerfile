FROM python:3.11-slim
WORKDIR /app
COPY server/ server/
COPY src/filedrawer/__init__.py src/filedrawer/index.py src/filedrawer/harvest.py src/filedrawer/badges.py src/filedrawer/screen.py src/filedrawer/pii.py src/filedrawer/
COPY src/filedrawer/vocab/constructs.json src/filedrawer/vocab/
COPY docs/ docs/
RUN mkdir -p studies   # no bundled demo on the hosted site; studies/ in the repo is test/demo material only
ARG FD_GIT_SHA=unknown
ENV PORT=8080 FD_DATA_DIR=/data PYTHONUNBUFFERED=1 FD_GIT_SHA=$FD_GIT_SHA
EXPOSE 8080
CMD ["python", "server/app.py"]
