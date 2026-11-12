FROM debian:bookworm-slim

# Debian packages: the libgpiod python bindings and smbus have no usable musl/alpine builds.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pil \
        python3-psutil \
        python3-smbus \
        python3-libgpiod \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY rackdisplay.py /app/rackdisplay.py

CMD ["python3", "-u", "/app/rackdisplay.py"]
