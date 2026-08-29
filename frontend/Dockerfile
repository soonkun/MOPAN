FROM node:20-slim
WORKDIR /app
# Build ARG, not a runtime ENV: `next build` resolves next.config.js rewrites()
# into .next/routes-manifest.json as a literal, and `next start` never re-reads
# the variable. It has to be present for the RUN npm run build below or the
# image proxies /api/* to its own empty port 8000.
ARG API_INTERNAL_URL=http://backend:8000
ENV API_INTERNAL_URL=${API_INTERNAL_URL}
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend .
RUN npm run build
EXPOSE 3000
RUN chown -R node:node /app
USER node
CMD ["npm", "start"]
