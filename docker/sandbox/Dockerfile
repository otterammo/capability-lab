FROM node:22.21.1-alpine3.23@sha256:0340fa682d72068edf603c305bfbc10e23219fb0e40df58d9ea4d6f33a9798bf

RUN npm install --global --ignore-scripts @earendil-works/pi-coding-agent@0.79.3 \
    && npm cache clean --force \
    && apk add --no-cache socat \
    && addgroup -g 65532 sandbox \
    && adduser -D -H -u 65532 -G sandbox sandbox \
    && mkdir /relay \
    && chown sandbox:sandbox /relay

USER 65532:65532
WORKDIR /workspace
