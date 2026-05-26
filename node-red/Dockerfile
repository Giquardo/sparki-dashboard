# ═══════════════════════════════════════════════════════════════════════
# Sparki Node-RED — custom image with InfluxDB + Postgres palettes
# ═══════════════════════════════════════════════════════════════════════
# Why a custom image:
# - Palettes pre-installed for reproducibility
# - Initial flows.json baked in so first boot works without a host
#   bind-mount (avoids Windows EBUSY rename issues on Deploy)
#
# After first deploy, Node-RED owns /data/flows.json and we can edit it
# via the visual UI. Re-build only when palettes or seed flow change.

FROM nodered/node-red:4.0-debian

# ─── Palettes ──────────────────────────────────────────────────────────
USER root

RUN cd /usr/src/node-red && \
    npm install --omit=dev --no-fund --no-audit --no-update-notifier \
        --prefix /usr/src/node-red \
        node-red-contrib-postgresql@0.15.4 && \
    chown -R node-red:root /usr/src/node-red/node_modules

# ─── Seed flow ─────────────────────────────────────────────────────────
# Bake the initial flow into a seed location. Our wrapper entrypoint
# (below) copies it to /data on first boot only.
COPY --chown=node-red:root flows/flows.json /seed/flows.json

# Wrapper script: seed flow if missing, then hand off to Node-RED's
# default startup command (`npm start ... --userDir /data`).
RUN printf '#!/bin/sh\n\
set -e\n\
if [ ! -f /data/flows.json ]; then\n\
    echo "[seed] /data/flows.json missing -- copying initial flow"\n\
    cp /seed/flows.json /data/flows.json\n\
else\n\
    echo "[seed] /data/flows.json already exists, keeping it"\n\
fi\n\
exec npm start --cache /data/.npm -- --userDir /data\n' \
    > /usr/local/bin/sparki-entrypoint.sh && \
    chmod +x /usr/local/bin/sparki-entrypoint.sh

USER node-red

# Override base image CMD to use our wrapper.
CMD ["/usr/local/bin/sparki-entrypoint.sh"]
