# RecallHub Frontend - Docker Image
# Multi-stage build for optimized production

# Stage 1: Build the React app
FROM node:20-alpine as builder

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies with frozen lockfile for reproducibility
RUN npm ci --prefer-offline --no-audit --no-fund

# Copy source code
COPY . .

# Build for production with optimization
RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:alpine as production

# Copy custom nginx configs
# Main config with rate limiting zones
COPY nginx.main.conf /etc/nginx/nginx.conf
# Server block config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy built assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Expose port
EXPOSE 80

# Health check - use curl which is available in nginx alpine
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD curl -f http://localhost:80/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
