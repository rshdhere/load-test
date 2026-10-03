# Build context: servers/
FROM node:24-slim
ENV NODE_ENV=production
WORKDIR /app/fastify
COPY fastify/package.json fastify/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY openapi.json /app/openapi.json
COPY fastify/src ./src
USER node
ENV PORT=3000
EXPOSE 3000
CMD ["node", "src/index.ts"]
