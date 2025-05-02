# Telegram Channel Forwarder Bot

A Python-based Telegram bot that uses the user's account to copy messages from forwarding-restricted channels to destination channels, with advanced text filtering and image replacement capabilities.

## Features

- Connect to Telegram API using user's account credentials
- Monitor specified source channel(s) for new messages
- Copy messages including text, media, and formatting from forwarding-restricted channels
- Send copied messages to specified destination channel where user is admin
- Handle various message types (text, images, videos, documents, etc.)
- Implement text filtering capabilities (keyword filtering, text replacement)
- Add image replacement options (substitute specific images or all images)
- Replace any image with caption with a specific "Billionaire AI BOT" image
- Web-based dashboard for easy configuration and monitoring
- Support for environment variables for secure deployment
- Deployment-ready for platforms like Render

## Requirements

- Python 3.11 or higher
- Telethon library
- Flask for the web interface
- PostgreSQL database (for production, SQLite for development)
- A Telegram account
- API credentials from https://my.telegram.org/apps

## Installation for Local Development

1. Clone this repository:
   ```
   git clone https://github.com/yourusername/telegram-forwarder.git
   cd telegram-forwarder
   ```

2. Create a virtual environment and install dependencies:
   ```
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Run the web interface:
   ```
   python main.py
   ```

4. Open the web interface at http://localhost:5000 and log in with default credentials (username: admin, password: admin)

5. Configure your API credentials, source and destination channels through the web interface.

## Deployment on Render

1. Fork or push this repository to your GitHub account.

2. Create a new Web Service on Render:
   - Connect to your GitHub repository
   - Set the Build Command: `pip install -r requirements.txt`
   - Set the Start Command: `gunicorn --bind 0.0.0.0:$PORT --reuse-port main:app`

3. Add the following environment variables:
   - `SESSION_SECRET`: A random string for Flask session security
   - `TELEGRAM_API_ID`: Your Telegram API ID (from https://my.telegram.org/apps)
   - `TELEGRAM_API_HASH`: Your Telegram API Hash
   - `TELEGRAM_PHONE`: Your phone number with country code (e.g., +12025550123)
   - `TELEGRAM_SOURCE_CHANNELS`: Comma-separated list of source channels (e.g., "Channel1:https://t.me/channel1,Channel2:https://t.me/channel2")
   - `TELEGRAM_DESTINATION_CHANNELS`: Comma-separated list of destination channels (e.g., "MyChannel:https://t.me/mychannel")
   - `TELEGRAM_MEDIA_ENABLED`: Set to "true" to enable media processing

4. Set up a PostgreSQL database on Render and link it to your web service.

5. After deployment, open the web interface and complete the authentication process.

## Security Considerations

- The bot runs using your personal Telegram account, so treat your credentials securely.
- Store Telegram API credentials and session files securely.
- Use environment variables instead of config files for sensitive information in production.
- Regularly update dependencies to address security vulnerabilities.
- Set a strong password for the web interface admin account.

## How It Works

1. The bot logs in to your Telegram account using the Telethon library.
2. It monitors the specified source channels for new messages.
3. When a new message is detected, it:
   - Filters and transforms the text based on your rules.
   - Processes the media as configured (e.g., replacing images with a default image).
   - Creates a new message in the destination channel with the processed content.
4. All operations are logged and can be monitored through the web interface.

## License

MIT License
