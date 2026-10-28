# AutoClip-Local: High-Throughput GPU-Accelerated Video Repurposing & Studio
FROM nvidia/cuda:12.2.0-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
    PATH="/opt/venv/bin:$PATH"

# Install system dependencies, FFmpeg with NVENC support, Python 3.11
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    curl \
    git \
    ffmpeg \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && add-apt-repository ppa:deadsnakes/ppa -y \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Set up clean Python 3.11 virtual environment
RUN python3.11 -m venv /opt/venv

WORKDIR /app

# Upgrade pip and install wheel
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .
RUN pip install --no-cache-dir -e .

# Create persistent storage directories
RUN mkdir -p /app/library/unpublished /app/library/published /app/raw_streams /app/stories /root/.config/autoclip/accounts

EXPOSE 8501

ENTRYPOINT ["autoclip"]
CMD ["studio", "--host", "0.0.0.0", "--port", "8501"]
