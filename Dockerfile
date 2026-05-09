FROM python:3.10-slim

WORKDIR /app

# Install system dependencies required for UI and worker's conda setup
RUN apt-get update && apt-get install -y \
    git \
    curl \
    wget \
    bzip2 \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Copy UI requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["python", "horde_ui.py"]
