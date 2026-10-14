FROM node:20-slim AS build

WORKDIR /app

COPY package.json .
RUN npm install

COPY . .
RUN npm run build

FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Optional file logging (LOG_TO_FILE) — strip CRs in case it was checked out on Windows.
COPY 40-file-logging.sh /docker-entrypoint.d/40-file-logging.sh
RUN sed -i 's/\r$//' /docker-entrypoint.d/40-file-logging.sh && chmod +x /docker-entrypoint.d/40-file-logging.sh

EXPOSE 80
