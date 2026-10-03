# Build context: servers/
FROM oven/bun:1.4-slim
ENV NODE_ENV=production
WORKDIR /app/bun
COPY bun/package.json bun/bun.lock ./
RUN bun install --frozen-lockfile --production
COPY openapi.json /app/openapi.json
COPY bun/src ./src
USER bun
ENV PORT=3000
EXPOSE 3000
CMD ["bun", "run", "src/index.ts"]
