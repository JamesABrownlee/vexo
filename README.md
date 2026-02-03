# Vexo - Smart Discord Music Bot

Vexo is a self-hosted Discord music bot with a focus on intelligent discovery, gapless playback, and user-driven recommendations.

## Core Features
- 🧠 **Vexo Discovery Engine**: Highly weighted recommendation system (+5 for 👍, -5 for 👎, -2 for skips).
- 🔄 **Gapless Playback**: Next-song buffering ensures music never stops.
- 🎯 **Session Influence**: The person interacting with the bot gets 1.2x weight on the autoplay recommendations.
- 🛠️ **Administrative Control**: Persistent bot owner and server admin roles for fine-grained settings.
- 📦 **Docker Ready**: Easy deployment with a single container and SQLite persistence.

## Setup Instructions

### 1. Prerequisites
- Docker and Docker Compose installed.
- A Discord Bot Token from the [Discord Developer Portal](https://discord.com/developers/applications).
- Give your bot `Message Content` and `Voice State` intents.

### 2. Configuration
Create a `.env` file in the root directory:
```env
DISCORD_TOKEN=your_token_here
YTDL_PO_TOKEN=your_po_token_here_if_needed
DATABASE_PATH=/app/data/vexo.db
```

### 3. Deployment
Run Vexo using Docker Compose:
```bash
docker-compose up -d
```

## Commands
- `/play <query>`: Play a specific song or search.
- `/just_play`: Start smart autoplay based on current audience.
- `/vexo_settings`: Manage server-specific settings (Admins only).
- `/set_admin_role <role>`: Designate a role as a Vexo Admin.
- 👍 / 👎: Vote on the now-playing message to influence future discovery.

## Data Persistence
The boat uses an SQLite database stored in `./data/vexo.db`. Ensure this folder has proper permissions for the Docker container.
