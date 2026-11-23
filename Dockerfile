# IoT Platform: satu image untuk semua layanan di compose.yml (web = Daphne, mqtt_bridge,
# rule_engine, housekeeping, coap_server, dan perintah manage.py sekali jalan).
# Fase 4 rencana container (repo infra, docs/rencana-migrasi-container.md).
#
# Dua tahap: tahap "build" memasang kompiler dan header MySQL untuk membangun mysqlclient,
# tahap akhir hanya membawa hasil jadinya, sehingga image lebih kecil dan tanpa kompiler.

FROM python:3.12-slim-bookworm AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential pkg-config default-libmysqlclient-dev \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r /tmp/requirements.txt

FROM python:3.12-slim-bookworm
# libmariadb3: pustaka klien MySQL untuk mysqlclient (juga untuk MySQL 8, caching_sha2_password).
# mosquitto: HANYA untuk program mosquitto_passwd, yang dipakai `manage.py mqtt_accounts` untuk
# meng-hash password akun broker (format $7$ Mosquitto 2.0, sama dengan broker di host).
# Broker-nya sendiri tidak dijalankan di image ini.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libmariadb3 mosquitto \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index /wheels/* && rm -rf /wheels

WORKDIR /app
COPY . .
# Pengguna bawaan bukan root. Di server, compose.yml menjalankannya dengan UID/GID user "iot"
# di host (APP_UID/APP_GID) supaya bisa menulis private_media/, staticfiles/, /srv/iot/mosquitto.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER 10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

CMD ["daphne", "--proxy-headers", "-b", "127.0.0.1", "-p", "8001", "config.asgi:application"]
