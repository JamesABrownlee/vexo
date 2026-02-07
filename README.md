# Smart Discord Music Bot

A self-hosted Discord music bot that learns your preferences and discovers music you'll love.

## ✨ Features

- **🎵 High-Quality Audio** - Direct Opus passthrough with FFmpegOpusAudio
- **🧠 Smart Discovery** - Three strategies: Similar songs, Same artist, Wildcard (charts)
- **👥 Democratic Selection** - Turn-based song picking for fair listening in voice channels
- **📥 Playlist Import** - Import from Spotify and YouTube to learn preferences
- **❤️ Reaction Learning** - Likes boost preferences, dislikes reduce them (skips don't count!)
- **🔒 Privacy Controls** - Export, delete, and opt-out of data tracking
- **📊 Web Dashboard** - Live stats and logs (localhost only)

## 🚀 Quick Start

### 1. Prerequisites

- Python 3.11+
- FFmpeg
- Discord Bot Token
- Spotify API credentials

### 2. Setup

```bash
# Clone the repository
git clone https://github.com/yourusername/smart-music-bot.git
cd smart-music-bot

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys
```

### 3. Run

```bash
python -m src.bot
```

## 🐳 Docker Deployment

```bash
# Build and run
docker-compose up -d

# View logs
docker-compose logs -f
```

The web dashboard is available at `http://localhost:8080` (localhost only, no auth).

## 📋 Commands

### Music
| Command | Description |
|---------|-------------|
| `/play song <query>` | Search and play a song |
| `/play any` | Start discovery mode |
| `/pause` | Pause playback |
| `/resume` | Resume playback |
| `/skip` | Vote to skip |
| `/queue` | Show the queue |
| `/nowplaying` | Show current song with discovery info |

### Preferences
| Command | Description |
|---------|-------------|
| `/like` | Like the current song |
| `/dislike` | Dislike the current song |
| `/preferences` | View your music preferences |
| `/import <url>` | Import Spotify/YouTube playlist |

### Privacy
| Command | Description |
|---------|-------------|
| `/privacy export` | Export all your data |
| `/privacy delete` | Delete all your data |
| `/privacy optout` | Opt out of preference tracking |
| `/privacy optin` | Re-enable preference tracking |

### Settings (Admin)
| Command | Description |
|---------|-------------|
| `/settings prebuffer <on/off>` | Toggle pre-buffering |
| `/settings discovery_weights` | Set strategy weights |
| `/settings show` | View current settings |
| `/dj @role` | Set DJ role |
| `/forceskip` | Force skip without voting |
| `/clear` | Clear the queue |

## ⚙️ Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `DISCORD_TOKEN` | Discord bot token | Required |
| `SPOTIFY_CLIENT_ID` | Spotify client ID | Required |
| `SPOTIFY_CLIENT_SECRET` | Spotify client secret | Required |
| `DATABASE_PATH` | SQLite database path | `./data/musicbot.db` |
| `WEB_HOST` | Dashboard host | `127.0.0.1` |
| `WEB_PORT` | Dashboard port | `8080` |
| `YTDL_COOKIES_PATH` | Path to YouTube cookies | Optional |

## 🎲 Discovery Strategies

The bot uses three weighted strategies to find songs:

1. **Similar Song (60%)** - Finds songs similar to ones you've liked
2. **Same Artist (10%)** - Picks a different song from an artist you enjoy
3. **Wildcard (30%)** - Random pick from current charts

Weights are configurable per server with `/settings discovery_weights`.

## 🔒 Privacy

- All data stored locally in SQLite 
- Users can export or delete their data anytime
- Opt-out stops all preference tracking
- Web dashboard is localhost-only with no authentication
- Skipping does NOT count as a dislike 

## 📝 License

MIT License - see LICENSE file for details.
