# 🐳 Vexo Container Deployment Guide

This guide covers deploying Vexo using the official Docker image from GitHub Container Registry.

**Image:** `ghcr.io/jamesabrownlee/vexo:latest`

---

## Prerequisites

1. **Docker** installed on your host system
2. **Discord Bot Token** from the [Discord Developer Portal](https://discord.com/developers/applications)
   - Enable `Message Content Intent` and `Server Members Intent` in Bot settings
3. **(Recommended)** A YouTube PO Token for improved reliability

---

## Quick Start with Docker Compose

### 1. Create your project folder

```bash
mkdir vexo && cd vexo
```

### 2. Create `docker-compose.yml`

```yaml
services:
  # Vexo Smart Discord Music Bot
  vexo:
    image: ghcr.io/jamesabrownlee/vexo:latest
    container_name: vexo
    restart: unless-stopped
    environment:
      - DISCORD_TOKEN=your_discord_token_here
      - DEFAULT_VOLUME=50
      - YTDL_COOKIES_PATH=/app/data/cookies.txt
      - YTDL_PO_TOKEN=your_po_token_here
      - FALLBACK_PLAYLIST=https://youtube.com/playlist?list=YOUR_PLAYLIST_ID
      - DATABASE_PATH=/app/data/vexo.db
    volumes:
      - ./data:/app/data
    labels:
      - "com.centurylinklabs.watchtower.enable=true"

  # Watchtower - Auto-update containers
  watchtower:
    image: containrrr/watchtower:latest
    container_name: watchtower
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      - WATCHTOWER_CLEANUP=true
      - WATCHTOWER_POLL_INTERVAL=300
      - WATCHTOWER_LABEL_ENABLE=true
      - DOCKER_API_VERSION=1.44
    command: --cleanup --label-enable
```

**Note:** Watchtower will automatically check for updates every 5 minutes and update the vexo container when a new image is pushed.

### 3. Deploy

```bash
docker compose up -d
```

### 4. Check logs

```bash
docker logs -f vexo
```

---

## Alternative: Docker Run

```bash
docker run -d \
  --name vexo \
  --restart unless-stopped \
  -e DISCORD_TOKEN=your_discord_token_here \
  -e DATABASE_PATH=/app/data/vexo.db \
  -v $(pwd)/data:/app/data \
  ghcr.io/jamesabrownlee/vexo:latest
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DISCORD_TOKEN` | ✅ | - | Your Discord bot token |
| `DATABASE_PATH` | ✅ | `/app/data/vexo.db` | Path to SQLite database |
| `DEFAULT_VOLUME` | ❌ | `50` | Default playback volume (0-100) |
| `YTDL_PO_TOKEN` | ❌ | - | YouTube PO Token for reliability |
| `YTDL_COOKIES_PATH` | ❌ | - | Path to YouTube cookies file |
| `FALLBACK_PLAYLIST` | ❌ | - | YouTube playlist URL for discovery fallback |
| `FFMPEG_BUFFER_SIZE` | ❌ | `512k` | FFmpeg buffer size (e.g., `1M`) |

---

## Data Persistence

Mount a volume to `/app/data` to persist:
- `vexo.db` - SQLite database (user preferences, discovery data)
- `cookies.txt` - (Optional) YouTube cookies for authentication

---

## Platform-Specific Instructions

### Unraid

1. Install **Docker Compose Manager** from Community Applications
2. Go to **Docker** → **Add Stack**
3. Name it `Vexo` and paste the compose configuration above
4. Click **Save** → **Compose Up**

### Portainer

1. Go to **Stacks** → **Add Stack**
2. Name it `vexo` and paste the compose configuration
3. Click **Deploy the stack**

### Synology DSM

1. Open **Container Manager** → **Project**
2. Click **Create** → paste compose configuration
3. Set the project path and click **Next** → **Done**

---

## Updating

Pull the latest image and recreate the container:

```bash
docker compose pull
docker compose up -d
```

Or with Docker run:

```bash
docker pull ghcr.io/jamesabrownlee/vexo:latest
docker stop vexo && docker rm vexo
# Run the docker run command again
```

---

## Troubleshooting

### Bot not connecting to Discord
- Verify your `DISCORD_TOKEN` is correct
- Check that required intents are enabled in the Developer Portal

### YouTube playback issues
- Try adding a `YTDL_PO_TOKEN`
- Place a valid `cookies.txt` in your data folder

### View logs
```bash
docker logs vexo --tail 100
```
