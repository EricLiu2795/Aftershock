FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY package*.json ./
COPY apps/api/package.json apps/api/package.json
COPY apps/web/package.json apps/web/package.json
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-bookworm-slim AS api
WORKDIR /app
ENV NODE_ENV=production HOST=0.0.0.0 PORT=3001 DATABASE_PATH=/data/aftershock.sqlite
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/apps/api ./apps/api
COPY --from=build /app/contracts ./contracts
COPY --from=build /app/fixtures ./fixtures
# Extraction resolves contracts relative to both src and dist under apps/api.
RUN mkdir /data && chown node:node /data
USER node
EXPOSE 3001
CMD ["node", "apps/api/dist/index.js"]

FROM caddy:2-alpine AS web
COPY --from=build /app/apps/web/dist /srv/web
COPY deploy/Caddyfile /etc/caddy/Caddyfile
