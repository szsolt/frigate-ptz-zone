FROM python:3.15-slim

WORKDIR /src
COPY pyproject.toml README.md ./
COPY frigate_ptz_zone ./frigate_ptz_zone
RUN pip install --no-cache-dir ".[fit]" && rm -rf /src

ENV DATA_DIR=/data PYTHONUNBUFFERED=1
WORKDIR /data
EXPOSE 8781
CMD ["frigate-ptz-zone"]
