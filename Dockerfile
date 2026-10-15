# Use official Python image as base
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy backend requirements and install them
COPY app/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy entire app folder
COPY app ./app

# Create necessary directories with proper permissions
RUN mkdir -p /app/data/metar_data /app/data/upper_air_data /app/data/ad_warn_data /app/logs && \
    chmod -R 755 /app/data /app/logs

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV FLASK_ENV=production

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=5 \
    CMD curl -f http://localhost:5000/health || exit 1

# Set working directory for Gunicorn
WORKDIR /app

# Start Flask app using Gunicorn with optimized settings
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--worker-class", "sync", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app.backend:create_app()"]
