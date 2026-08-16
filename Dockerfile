# Syn Bank Coverage Desk — self-contained image.
#
# Everything is inside: the raw inputs (restored from data/data.tgz), the full
# stage 1-4 pipeline output, the dashboard, and the copilot .env.
#
# >>> THIS IMAGE CONTAINS A LIVE API KEY. <<<
# `.env` is copied in by explicit request, so the DEEPSEEK_API_KEY is baked into
# a layer and `docker history` / `docker save` / any registry push exposes it.
# Keep this image local or in a private registry, and rotate the key if it
# escapes. To build a key-free image instead:
#     docker build --build-arg BAKE_ENV=0 -t syn-wallet .
# and pass the key at run time with `-e DEEPSEEK_API_KEY=...`.

FROM python:3.13-slim

# 1 = copy .env into the image (default, as requested). 0 = leave it out.
ARG BAKE_ENV=1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

WORKDIR /app

# Dependencies first, so a source edit does not reinstall duckdb and friends.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The application. .dockerignore keeps .venv, .git and the 409MB of raw CSVs out.
COPY . .

# Restore the raw inputs and run the whole pipeline at BUILD time, so the
# container starts instantly and the served process only ever reads Parquet.
#   stage 1 cleaning -> stage 2 features -> stage 3 wallet -> stage 4 intelligence
# --sensitivity rebuilds the engine 36 times; the dashboard's range marks read
# those tables, so it is not optional here.
RUN tar -xzf data/data.tgz -C data/ \
 && find data -name '._*' -delete \
 && python -m src.syn_wallet.clean_data --overwrite \
 && python -m src.syn_wallet.build_features --overwrite \
 && python -m src.syn_wallet.build_wallet --overwrite --sensitivity \
 && python -m src.syn_wallet.build_intelligence --overwrite \
 && rm -f data/transactional_banking.csv \
          data/cross_border_payments.csv \
          data/trade_finance.csv \
          data/data.tgz

# The copilot key. Read from the environment at call time; `.env` is loaded at
# import by copilot/config.py. Dropped when BAKE_ENV=0.
RUN if [ "$BAKE_ENV" = "0" ]; then rm -f .env; fi

# Runs unprivileged. Nothing in the served path writes to disk.
RUN useradd --create-home --uid 10001 syn && chown -R syn:syn /app
USER syn

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ['PORT']+'/api/health').read(1)"

# --host 0.0.0.0 because the serve default is loopback-only and would be
# unreachable from outside the container.
CMD ["sh", "-c", "exec python -m src.syn_wallet.serve --host 0.0.0.0 --port ${PORT}"]
