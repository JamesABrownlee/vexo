# Vexo Development Environment - Quick Start Guide

## ✅ Setup Complete!

Your Vexo development environment is ready. Here's what was installed:
- ✅ Git 2.52.0
- ✅ Python 3.12.10
- ✅ Virtual environment created at `./venv`
- ✅ All dependencies installed (discord.py, yt-dlp, aiosqlite, etc.)
- ✅ `.env` configuration file created
- ✅ Data directory created for database

---

## 🚀 Getting Started

### 1. Configure Your Bot Token
Edit the `.env` file and add your Discord bot token:
```
DISCORD_TOKEN=your_actual_token_here
```

Get your token from: https://discord.com/developers/applications

**Required Bot Intents:**
- Message Content Intent
- Server Members Intent
- Presence Intent

### 2. Run the Bot

**Activate the virtual environment (PowerShell):**
```powershell
.\venv\Scripts\Activate.ps1
```

**Run the bot:**
```powershell
python bot.py
```

### 3. Stop the Bot
Press `Ctrl+C` in the terminal

---

## 🔨 Development Workflow

### Making Changes
1. Edit your code files
2. Test locally by running `python bot.py`
3. Stage your changes: `git add .`
4. Commit: `git commit -m "Description of changes"`
5. Push to GitHub: `git push origin main`

### Pull Latest Changes
```powershell
git pull origin main
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt  # if dependencies changed
```

### View Git Status
```powershell
git status
```

### View Commit History
```powershell
git log --oneline
```

---

## 🐳 Docker Build & Deploy

### Build Docker Image Locally
```powershell
docker build -t vexo:local .
```

### Run with Docker Compose
```powershell
docker-compose up -d
```

### View Logs
```powershell
docker-compose logs -f vexo
```

### Stop Container
```powershell
docker-compose down
```

---

## 📁 Project Structure

```
vexo/
├── bot.py              # Main bot entry point
├── config.py           # Configuration management
├── database.py         # Database operations
├── cogs/               # Bot command modules
│   └── ...
├── utils/              # Utility functions
│   └── ...
├── data/               # Database storage (SQLite)
│   └── vexo.db
├── venv/               # Python virtual environment
├── .env                # Environment variables (DON'T COMMIT!)
├── requirements.txt    # Python dependencies
├── Dockerfile          # Docker image build
└── docker-compose.yml  # Docker deployment config
```

---

## 🔑 Important Notes

- **Never commit your `.env` file** - it contains your bot token (already in .gitignore)
- The `data/` directory contains your SQLite database
- The `venv/` directory is your Python virtual environment (excluded from git)
- Always activate the virtual environment before running the bot locally

---

## 🆘 Troubleshooting

**Bot won't start:**
- Check that your Discord token is correct in `.env`
- Ensure all intents are enabled in Discord Developer Portal
- Verify dependencies are installed: `pip list`

**Import errors:**
- Make sure virtual environment is activated
- Reinstall dependencies: `pip install -r requirements.txt`

**Git push rejected:**
- You may need to authenticate with GitHub
- Use GitHub CLI: `gh auth login`
- Or use a Personal Access Token

---

## 📚 Useful Commands

| Command | Description |
|---------|-------------|
| `python bot.py` | Run the bot locally |
| `git status` | Check working tree status |
| `git add .` | Stage all changes |
| `git commit -m "msg"` | Commit with message |
| `git push` | Push to GitHub |
| `git pull` | Pull latest changes |
| `pip list` | Show installed packages |
| `pip install -r requirements.txt` | Install dependencies |

---

Happy coding! 🎵🤖
