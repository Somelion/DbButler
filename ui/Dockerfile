FROM node:24-slim AS build

WORKDIR /app

# npm ci installs exactly what package-lock.json records (and fails if it's
# out of sync with package.json) — reproducible builds, unlike npm install.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build

FROM nginx:1.30-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Optional file logging (LOG_TO_FILE) — strip CRs in case it was checked out on Windows.
COPY 40-file-logging.sh /docker-entrypoint.d/40-file-logging.sh
RUN sed -i 's/\r$//' /docker-entrypoint.d/40-file-logging.sh && chmod +x /docker-entrypoint.d/40-file-logging.sh

EXPOSE 80
