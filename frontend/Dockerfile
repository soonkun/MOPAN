FROM node:20-slim
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend .
RUN npm run build
EXPOSE 3000
RUN chown -R node:node /app
USER node
CMD ["npm", "start"]
