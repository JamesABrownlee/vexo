# Deploying Vexo on Unraid

Since you've pushed to GitHub and the CI/CD pipeline is building your image, the easiest way to deploy Vexo on Unraid is using the **Docker Compose Manager** and your new GHCR image.

## 1. Prerequisites
- Ensure you have the **Docker Compose Manager** plugin installed from the CA (Community Applications) store.
- Your GHCR image location: `ghcr.io/axiom3d-yt/vexo:latest`

## 2. Setup on Unraid

1.  Go to the **Docker** tab in Unraid.
2.  Click **Add Stack** (in the Compose section).
3.  Name it `Vexo`.
4.  Paste the following `docker-compose.yml` configuration:

```yaml
services:
  vexo:
    image: ghcr.io/axiom3d-yt/vexo:latest
    container_name: vexo
    restart: unless-stopped
    environment:
      - DISCORD_TOKEN=your_discord_token_here
      - DEFAULT_VOLUME=50
      - YTDL_PO_TOKEN=your_po_token_here
      - DATABASE_PATH=/app/data/vexo.db
      # Optional: if you upload cookies.txt to your appdata folder
      - YTDL_COOKIES_PATH=/app/data/cookies.txt
    volumes:
      - /mnt/user/appdata/vexo:/app/data
```

## 3. Configuration

### Persistent Data
The configuration above maps `/mnt/user/appdata/vexo` to the bot's data folder. 
- The SQLite database (`vexo.db`) will be created here automatically.
- **Tip**: If you have a `cookies.txt` for YouTube, place it directly in `/mnt/user/appdata/vexo/` so the bot can find it.

### Environment Variables
Replace the following placeholders in the compose file:
- `your_discord_token_here`: Your bot token.
- `your_po_token_here`: (Highly Recommended) Your YouTube PO Token for better reliability.

## 4. Launch
1.  Click **Save**.
2.  Click **Compose Up**.
3.  The bot should pull the image from GHCR and start up. Check the logs in the Compose UI to verify it connected to Discord.

## 5. Updates
Whenever you push new code to your GitHub repo, the CI/CD will build a new image. To update on Unraid:
1.  Go to the Vexo stack.
2.  Click **Compose Pull**.
3.  Click **Compose Up**.
