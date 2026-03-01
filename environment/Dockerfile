FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-redis \
    redis-server \
    supervisor \
    bash \
    curl \
    jq \
    psmisc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN touch /var/log/consumer.log && chmod 666 /var/log/consumer.log
