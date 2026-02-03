# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Install FFmpeg and basic utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Deno (Required by latest yt-dlp for YouTube JS challenges)
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Run the bot
CMD ["python", "bot.py"]
