# Stonkfly worker + watch page. One image, two commands (see deploy/entrypoint.sh).
FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends g++ ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY stonkfly ./stonkfly
COPY site ./site
COPY deploy/entrypoint.sh /usr/local/bin/stonkfly-entrypoint
RUN pip install --no-cache-dir . && chmod +x /usr/local/bin/stonkfly-entrypoint

# The dataset (built once, ~1.6 GB) and run state live on volumes.
ENV STONKFLY_DATA=/data \
    STONKFLY_RUNS=/runs \
    STONKFLY_SITE=/app/site \
    OPENBLAS_NUM_THREADS=1 \
    PYTHONUNBUFFERED=1
VOLUME ["/data", "/runs"]
EXPOSE 8787

ENTRYPOINT ["stonkfly-entrypoint"]
CMD ["worker"]
