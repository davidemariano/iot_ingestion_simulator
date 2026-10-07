# Dashboard IoT: build Angular, poi nginx che serve i file statici e inoltra /api al simulatore.

# --- build -----------------------------------------------------------------------
FROM node:22-alpine AS build
ENV NG_CLI_ANALYTICS=false \
    CI=true
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npx ng build --configuration production

# --- runtime ---------------------------------------------------------------------
FROM nginxinc/nginx-unprivileged:1.30-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/dashboard/browser /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
