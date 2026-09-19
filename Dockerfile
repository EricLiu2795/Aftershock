FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY package*.json ./
COPY apps/api/package.json apps/api/package.json
COPY apps/web/package.json apps/web/package.json
RUN npm ci
COPY . .
ARG VITE_MAPBOX_ACCESS_TOKEN
ENV VITE_MAPBOX_ACCESS_TOKEN=${VITE_MAPBOX_ACCESS_TOKEN}
RUN npm run build

FROM node:22-bookworm-slim AS api
WORKDIR /app
ENV NODE_ENV=production HOST=0.0.0.0 PORT=3001 DATABASE_PATH=/data/aftershock.sqlite
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/apps/api ./apps/api
COPY --from=build /app/contracts ./contracts
COPY --from=build /app/fixtures ./fixtures
COPY --from=build /app/ingestion ./ingestion
COPY --from=build /app/requirements.txt ./requirements.txt
COPY --from=build /app/data/graph_exports/caraballeda.geojson ./data/graph_exports/caraballeda.geojson
COPY --from=build /app/data/social_replay_caraballeda.json ./data/social_replay_caraballeda.json
COPY --from=build /app/data/ingestion_ai_responses.json ./data/ingestion_ai_responses.json
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHON_BIN=/opt/venv/bin/python
# Extraction resolves contracts relative to both src and dist under apps/api.
RUN mkdir /data && chown node:node /data
USER node
EXPOSE 3001
CMD ["node", "apps/api/dist/index.js"]

FROM caddy:2-alpine AS web
COPY --from=build /app/apps/web/dist /srv/web
COPY deploy/Caddyfile /etc/caddy/Caddyfile
