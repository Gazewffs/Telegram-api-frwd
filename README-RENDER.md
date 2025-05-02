# Telegram Forwarder Bot - Render Deployment Guide

This guide will walk you through deploying the Telegram Forwarder Bot on Render.com.

## Prerequisites

1. A Telegram account with [API credentials](https://my.telegram.org/apps)
2. A GitHub repository with your bot code
3. A Render.com account

## Deployment Steps

### 1. Prepare Your GitHub Repository

Make sure your repository includes all necessary files:
- All Python files (main.py, bot.py, etc.)
- Template files in `/templates/` directory
- Static files in `/static/` directory including the replacement image
- config.template.ini (without your sensitive data)
- render.yaml (deployment configuration)
- render-requirements.txt (with lowercase "telethon", not "Telethon")
- Procfile
- runtime.txt

**Important Note About Session Files:**
If you have already authenticated with Telegram on a local machine, you can include your existing session file from the `/sessions/` directory in your repository. This will allow the bot to start without requiring re-authentication on Render. If no session file is included, you will need to complete the authentication process after deployment.

### 2. Connect Your Repository to Render

1. Log in to your Render dashboard
2. Click "New" and select "Blueprint"
3. Connect your GitHub repository
4. When prompted, confirm that you want to use the render.yaml configuration

### 3. Configure Environment Variables

Render will automatically set up a PostgreSQL database and generate a random SESSION_SECRET. You need to add:

1. TELEGRAM_API_ID - Your Telegram API ID
2. TELEGRAM_API_HASH - Your Telegram API Hash
3. TELEGRAM_PHONE - Your phone number with country code (e.g., +12345678901)

### 4. Wait for Initial Deployment

Render will build and deploy your application. This may take a few minutes.

### 5. Complete Telegram Authentication

1. Once deployed, visit your app URL
2. Navigate to the Authentication page
3. You'll be prompted to enter your verification code (sent to your Telegram app)
4. If you have 2FA enabled, you'll also need to enter your password

### 6. Configure Channels

1. Navigate to the Channel Settings page
2. Add your source channels (where to get messages from)
3. Add your destination channels (where to forward messages to)
4. Click Save

## Performance Optimizations

This bot includes several performance enhancements:
- Parallel processing for both source and destination channels
- Reduced check interval (1 second) for faster forwarding
- Configurable batch size for message fetching
- Efficient image replacement system

## Troubleshooting

- **Authentication Issues**: If you need to re-authenticate, visit the Authentication page
- **Database Errors**: Check Render logs for database connection issues
- **Dependency Errors**: Make sure render-requirements.txt uses lowercase "telethon" instead of "Telethon"
- **Forwarding Not Working**: Verify channel IDs and ensure the user account has access to both source and destination channels
- **Application Not Starting**: Check that environment variable names are correct - TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE (not API_ID, API_HASH, PHONE_NUMBER)

## Security Notes

- Never commit your actual config.ini file with API credentials
- Be careful with session files: While including them in your repository enables automatic authentication, they do contain sensitive authentication data. For maximum security, consider using a private repository or authenticating manually after deployment.
- Use environment variables for all sensitive information