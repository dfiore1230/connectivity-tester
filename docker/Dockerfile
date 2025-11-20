FROM alpine:3.20

# Install ping, bash, Python, curl (for public IP lookup)
RUN apk add --no-cache iputils bash libc-utils python3 curl

# Create log directory
RUN mkdir -p /logs

# Copy scripts and static assets into container (from repo root context)
COPY app/connectivity.sh /usr/local/bin/connectivity.sh
COPY app/webserver.py   /usr/local/bin/webserver.py
COPY app/static         /usr/local/bin/static
COPY docker/start.sh    /usr/local/bin/start.sh

RUN chmod +x /usr/local/bin/connectivity.sh \
             /usr/local/bin/start.sh \
             /usr/local/bin/webserver.py

# Environment variables (override at runtime if needed)
ENV TARGET_HOST=8.8.8.8           \
    TARGETS=                      \
    INTERVAL_SECONDS=30           \
    WEB_PORT=8080

WORKDIR /logs
EXPOSE 8080

CMD ["/usr/local/bin/start.sh"]
