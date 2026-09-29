FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY gateway_service.py .

# Create non-root user
RUN useradd -m -u 1000 gateway && chown -R gateway:gateway /app
USER gateway

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import requests; requests.get('http://localhost:5454/health')"

# Environment defaults
ENV RELAY_IP=192.168.1.100
ENV RELAY_PASSWORD=12345
ENV SERVER_URL=http://localhost:8000
ENV DEVICE_TOKEN=gateway-token
ENV GATEWAY_PORT=5454

EXPOSE 5454

CMD ["python3", "gateway_service.py"]