#!/usr/bin/env python3
import logging
import sys
import os
import threading
import time
from configparser import ConfigParser
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from datetime import datetime

# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("Loaded environment variables from .env file")
except ImportError:
    pass  # dotenv not installed, skip loading
    
# Ensure required directories exist
def ensure_required_directories():
    """Create directories required by the application if they don't exist"""
    directories = ['sessions', 'static/images']
    for directory in directories:
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
            print(f"Created directory: {directory}")
            
    # Create default image if it doesn't exist
    billionaire_bot_image = 'static/images/billionaire_bot.png'
    if not os.path.exists(billionaire_bot_image):
        try:
            from PIL import Image, ImageDraw, ImageFont
            
            # Create a basic solid color image with text
            img = Image.new('RGB', (800, 600), color=(0, 64, 128))
            
            # Add text
            draw = ImageDraw.Draw(img)
            text = "Billionaire AI BOT"
            
            # Simple text placement - no fancy methods that might cause issues
            draw.text((320, 280), text, fill=(255, 255, 255))
            
            # Save the image
            img.save(billionaire_bot_image)
            print(f"Created default Billionaire BOT image at: {billionaire_bot_image}")
        except Exception as e:
            print(f"Could not create default image: {e}")

# Ensure required directories and files exist at startup
ensure_required_directories()

# Set up logging
def setup_logging():
    """Configure logging for the application"""
    log_level = logging.DEBUG if os.environ.get('DEBUG') else logging.INFO
    
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Set telethon logger to warning to reduce noise
    logging.getLogger('telethon').setLevel(logging.WARNING)
    
    return logging.getLogger('telegram_forwarder')

logger = setup_logging()

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev_secret_key_change_in_production")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Configure the database
database_url = os.environ.get("DATABASE_URL", "sqlite:///telegram_forwarder.db")
# Fix for Postgres DATABASE_URL from Render (uses postgres:// instead of postgresql://)
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}

# Initialize SQLAlchemy
class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)
db.init_app(app)

# Import bot related modules
from bot import TelegramForwarderBot

# Global variables
bot_instance = None
bot_thread = None
bot_running = False
bot_status_messages = []

# Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class BotLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    level = db.Column(db.String(10))
    message = db.Column(db.Text)
    
    @classmethod
    def add_log(cls, level, message):
        log = cls(level=level, message=message)
        db.session.add(log)
        db.session.commit()
        return log

# Custom logging handler to save logs to database
class DatabaseLogHandler(logging.Handler):
    def emit(self, record):
        # Only save to database if we're in application context
        try:
            if app.app_context.top is not None:
                with app.app_context():
                    BotLog.add_log(record.levelname, self.format(record))
        except Exception:
            pass  # Skip database logging if not in app context
        
        # Always add to status messages for the UI (this doesn't require app context)
        global bot_status_messages
        bot_status_messages.append({
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'level': record.levelname,
            'message': self.format(record)
        })
        
        # Keep only the last 100 messages
        if len(bot_status_messages) > 100:
            bot_status_messages = bot_status_messages[-100:]

# Helper functions
def get_config():
    """Load configuration from config.ini and/or environment variables"""
    config = ConfigParser()
    
    # Try to read from config.ini file first
    config_file = os.environ.get('CONFIG_FILE', 'config.ini')
    if os.path.exists(config_file):
        config.read(config_file)
    
    # Override with environment variables if available
    # Auth section
    if not config.has_section('Auth'):
        config.add_section('Auth')
    
    if os.environ.get('TELEGRAM_API_ID'):
        config['Auth']['api_id'] = os.environ.get('TELEGRAM_API_ID', '')
    if os.environ.get('TELEGRAM_API_HASH'):
        config['Auth']['api_hash'] = os.environ.get('TELEGRAM_API_HASH', '')
    if os.environ.get('TELEGRAM_PHONE'):
        config['Auth']['phone_number'] = os.environ.get('TELEGRAM_PHONE', '')
    
    # Channels section
    if not config.has_section('Channels'):
        config.add_section('Channels')
    
    if os.environ.get('TELEGRAM_SOURCE_CHANNELS'):
        config['Channels']['sources'] = os.environ.get('TELEGRAM_SOURCE_CHANNELS', '')
    if os.environ.get('TELEGRAM_DESTINATION_CHANNELS'):
        config['Channels']['destinations'] = os.environ.get('TELEGRAM_DESTINATION_CHANNELS', '')
    
    # Media section
    if not config.has_section('Media'):
        config.add_section('Media')
    
    if os.environ.get('TELEGRAM_MEDIA_ENABLED'):
        media_enabled = os.environ.get('TELEGRAM_MEDIA_ENABLED', 'true')
        config['Media']['media_enabled'] = media_enabled.lower()
        
    # TextFilters section
    if not config.has_section('TextFilters'):
        config.add_section('TextFilters')
        config['TextFilters']['enabled'] = 'true'
        
    # Advanced section
    if not config.has_section('Advanced'):
        config.add_section('Advanced')
        config['Advanced']['preserve_formatting'] = 'true'
        config['Advanced']['copy_media'] = 'true'
        config['Advanced']['session_path'] = 'sessions/user_session'
    
    return config

def save_config(config):
    """Save configuration to config.ini"""
    config_file = os.environ.get('CONFIG_FILE', 'config.ini')
    try:
        with open(config_file, 'w') as configfile:
            config.write(configfile)
    except Exception as e:
        logger.error(f"Error saving config: {e}")
        # In production environments like Render, we might not be able to write to the file
        # In this case, just log the error and continue using environment variables

def start_bot_thread():
    """Start the bot in a separate thread"""
    global bot_instance, bot_thread, bot_running
    
    if bot_thread and bot_thread.is_alive():
        logger.warning("Bot is already running")
        return False
    
    config = get_config()
    
    # Check if required config is set
    if not config.has_section('Auth') or not config['Auth']['api_id'] or not config['Auth']['api_hash']:
        logger.error("API credentials not configured")
        return False
    
    # Create and start bot
    try:
        bot_instance = TelegramForwarderBot(config)
        bot_thread = threading.Thread(target=bot_instance.run)
        bot_thread.daemon = True
        bot_thread.start()
        bot_running = True
        logger.info("Bot started in background thread")
        return True
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        return False

def stop_bot_thread():
    """Stop the bot thread"""
    global bot_instance, bot_running
    
    if bot_instance:
        bot_instance.is_running = False
        bot_running = False
        logger.info("Bot stop signal sent")
        return True
    return False

# Routes
@app.route('/')
def index():
    """Home page"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Check if the bot is already authorized - if so, don't redirect to verification
    is_authorized = False
    if bot_instance and hasattr(bot_instance, 'session_manager'):
        try:
            auth_status = bot_instance.session_manager.get_auth_status()
            if auth_status.get('status') == 'authorized':
                is_authorized = True
                # Clear any verification flags if already authorized
                session.pop('needs_verification', None)
                session.pop('needs_2fa', None)
        except Exception as e:
            logger.error(f"Error checking auth status: {e}")
    
    # Only redirect to verification if bot is running but not authorized
    if bot_running and not is_authorized:
        # Redirect to verification page if needed
        if session.get('needs_verification'):
            return redirect(url_for('verification_code'))
        
        # Redirect to 2FA page if needed
        if session.get('needs_2fa'):
            return redirect(url_for('twofa_password'))
        
    config = get_config()
    return render_template(
        'index.html', 
        bot_running=bot_running,
        is_authorized=is_authorized,
        api_configured='Auth' in config and config['Auth']['api_id'] and config['Auth']['api_hash'],
        channels_configured='Channels' in config and config['Channels']['sources'] and config['Channels']['destinations']
    )

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            session['user_id'] = user.id
            session['is_admin'] = user.is_admin
            flash('Logged in successfully!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Logout user"""
    session.pop('user_id', None)
    session.pop('is_admin', None)
    flash('Logged out successfully', 'success')
    return redirect(url_for('login'))

@app.route('/api-settings', methods=['GET', 'POST'])
def api_settings():
    """API credentials settings page"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    config = get_config()
    
    if request.method == 'POST':
        # Update API settings
        if not config.has_section('Auth'):
            config.add_section('Auth')
        
        config['Auth']['api_id'] = request.form.get('api_id', '')
        config['Auth']['api_hash'] = request.form.get('api_hash', '')
        config['Auth']['phone_number'] = request.form.get('phone_number', '')
        
        save_config(config)
        flash('API settings updated successfully', 'success')
        return redirect(url_for('api_settings'))
    
    # Get current values
    api_id = config['Auth']['api_id'] if config.has_section('Auth') and 'api_id' in config['Auth'] else ''
    api_hash = config['Auth']['api_hash'] if config.has_section('Auth') and 'api_hash' in config['Auth'] else ''
    phone_number = config['Auth']['phone_number'] if config.has_section('Auth') and 'phone_number' in config['Auth'] else ''
    
    return render_template(
        'api_settings.html',
        api_id=api_id,
        api_hash=api_hash,
        phone_number=phone_number
    )

@app.route('/channel-settings', methods=['GET', 'POST'])
def channel_settings():
    """Channel configuration page"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    config = get_config()
    
    if request.method == 'POST':
        # Update channel settings
        if not config.has_section('Channels'):
            config.add_section('Channels')
        
        config['Channels']['sources'] = request.form.get('sources', '')
        config['Channels']['destinations'] = request.form.get('destinations', '')
        
        save_config(config)
        flash('Channel settings updated successfully', 'success')
        return redirect(url_for('channel_settings'))
    
    # Get current values
    sources = config['Channels']['sources'] if config.has_section('Channels') and 'sources' in config['Channels'] else ''
    destinations = config['Channels']['destinations'] if config.has_section('Channels') and 'destinations' in config['Channels'] else ''
    
    return render_template(
        'channel_settings.html',
        sources=sources,
        destinations=destinations
    )

@app.route('/filter-settings', methods=['GET', 'POST'])
def filter_settings():
    """Text filter settings page"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    config = get_config()
    
    if request.method == 'POST':
        # Update filter settings
        if not config.has_section('TextFilters'):
            config.add_section('TextFilters')
        
        config['TextFilters']['enabled'] = 'true' if request.form.get('enabled') == 'on' else 'false'
        config['TextFilters']['blacklist_keywords'] = request.form.get('blacklist_keywords', '')
        config['TextFilters']['replacements'] = request.form.get('replacements', '')
        config['TextFilters']['regex_enabled'] = 'true' if request.form.get('regex_enabled') == 'on' else 'false'
        config['TextFilters']['regex_patterns'] = request.form.get('regex_patterns', '')
        
        save_config(config)
        flash('Text filter settings updated successfully', 'success')
        return redirect(url_for('filter_settings'))
    
    # Get current values
    if not config.has_section('TextFilters'):
        config.add_section('TextFilters')
        
    enabled = config.getboolean('TextFilters', 'enabled', fallback=True)
    blacklist_keywords = config['TextFilters']['blacklist_keywords'] if 'blacklist_keywords' in config['TextFilters'] else ''
    replacements = config['TextFilters']['replacements'] if 'replacements' in config['TextFilters'] else ''
    regex_enabled = config.getboolean('TextFilters', 'regex_enabled', fallback=False)
    regex_patterns = config['TextFilters']['regex_patterns'] if 'regex_patterns' in config['TextFilters'] else ''
    
    return render_template(
        'filter_settings.html',
        enabled=enabled,
        blacklist_keywords=blacklist_keywords,
        replacements=replacements,
        regex_enabled=regex_enabled,
        regex_patterns=regex_patterns
    )

@app.route('/media-settings', methods=['GET', 'POST'])
def media_settings():
    """Media processing settings page"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    config = get_config()
    
    if request.method == 'POST':
        # Update media settings
        if not config.has_section('Media'):
            config.add_section('Media')
        
        config['Media']['media_enabled'] = 'true' if request.form.get('media_enabled') == 'on' else 'false'
        config['Media']['replace_all_images'] = 'true' if request.form.get('replace_all_images') == 'on' else 'false'
        config['Media']['replacement_image_path'] = request.form.get('replacement_image_path', '')
        config['Media']['image_replacements'] = request.form.get('image_replacements', '')
        
        save_config(config)
        flash('Media settings updated successfully', 'success')
        return redirect(url_for('media_settings'))
    
    # Get current values
    if not config.has_section('Media'):
        config.add_section('Media')
        
    media_enabled = config.getboolean('Media', 'media_enabled', fallback=True)
    replace_all_images = config.getboolean('Media', 'replace_all_images', fallback=False)
    replacement_image_path = config['Media']['replacement_image_path'] if 'replacement_image_path' in config['Media'] else ''
    image_replacements = config['Media']['image_replacements'] if 'image_replacements' in config['Media'] else ''
    
    return render_template(
        'media_settings.html',
        media_enabled=media_enabled,
        replace_all_images=replace_all_images,
        replacement_image_path=replacement_image_path,
        image_replacements=image_replacements
    )

@app.route('/advanced-settings', methods=['GET', 'POST'])
def advanced_settings():
    """Advanced settings page"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    config = get_config()
    
    if request.method == 'POST':
        # Update advanced settings
        if not config.has_section('Advanced'):
            config.add_section('Advanced')
        
        config['Advanced']['session_path'] = request.form.get('session_path', 'sessions/user_session')
        config['Advanced']['check_interval'] = request.form.get('check_interval', '5')
        config['Advanced']['max_message_age'] = request.form.get('max_message_age', '24')
        config['Advanced']['preserve_formatting'] = 'true' if request.form.get('preserve_formatting') == 'on' else 'false'
        config['Advanced']['copy_media'] = 'true' if request.form.get('copy_media') == 'on' else 'false'
        config['Advanced']['verbose_logging'] = 'true' if request.form.get('verbose_logging') == 'on' else 'false'
        
        save_config(config)
        flash('Advanced settings updated successfully', 'success')
        return redirect(url_for('advanced_settings'))
    
    # Get current values
    if not config.has_section('Advanced'):
        config.add_section('Advanced')
        
    session_path = config['Advanced']['session_path'] if 'session_path' in config['Advanced'] else 'sessions/user_session'
    check_interval = config['Advanced']['check_interval'] if 'check_interval' in config['Advanced'] else '5'
    max_message_age = config['Advanced']['max_message_age'] if 'max_message_age' in config['Advanced'] else '24'
    preserve_formatting = config.getboolean('Advanced', 'preserve_formatting', fallback=True)
    copy_media = config.getboolean('Advanced', 'copy_media', fallback=True)
    verbose_logging = config.getboolean('Advanced', 'verbose_logging', fallback=True)
    
    return render_template(
        'advanced_settings.html',
        session_path=session_path,
        check_interval=check_interval,
        max_message_age=max_message_age,
        preserve_formatting=preserve_formatting,
        copy_media=copy_media,
        verbose_logging=verbose_logging
    )

@app.route('/bot/start', methods=['POST'])
def start_bot():
    """Start the bot"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'})
    
    success = start_bot_thread()
    return jsonify({'success': success, 'message': 'Bot started' if success else 'Failed to start bot'})

@app.route('/bot/stop', methods=['POST'])
def stop_bot():
    """Stop the bot"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'})
    
    success = stop_bot_thread()
    return jsonify({'success': success, 'message': 'Bot stopped' if success else 'Failed to stop bot'})

@app.route('/bot/status', methods=['GET'])
def bot_status():
    """Get bot status and latest logs"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'})
    
    response_data = {
        'success': True,
        'running': bot_running,
        'logs': bot_status_messages[-20:] if bot_status_messages else []
    }
    
    # Check if there's a pending Telegram auth
    if bot_instance and hasattr(bot_instance, 'session_manager'):
        auth_status = bot_instance.session_manager.get_auth_status()
        if auth_status['status'] != 'none':
            response_data['auth_status'] = auth_status
            
            # If we're waiting for a verification code, set session flag
            if auth_status['status'] == 'waiting_for_code':
                session['needs_verification'] = True
            elif auth_status['status'] == 'waiting_for_password':
                session['needs_2fa'] = True
            else:
                session.pop('needs_verification', None)
                session.pop('needs_2fa', None)
            
    return jsonify(response_data)

@app.route('/verification-code', methods=['GET', 'POST'])
def verification_code():
    """Handle Telegram verification code entry"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Make sure bot is running if not already started
    global bot_instance, bot_running
    
    # Always try to start the bot to make sure it's running for verification
    if not bot_instance or not bot_running:
        logger.info("Starting bot for verification")
        success = start_bot_thread()
        if not success:
            flash('Failed to start bot. Check your API settings.', 'danger')
            return render_template('verification_code.html', error='Failed to start bot. Check your API settings.')
    
    # Set flag for web authentication regardless of whether bot was just started
    if bot_instance and hasattr(bot_instance, 'session_manager'):
        bot_instance.session_manager.auth_web_mode = True
    
    if request.method == 'POST':
        code = request.form.get('code')
        if not code:
            return render_template('verification_code.html', error='Verification code is required')
        
        if not bot_instance or not hasattr(bot_instance, 'session_manager'):
            # Try to start the bot again if it's not properly initialized
            success = start_bot_thread()
            if not success or not bot_instance or not hasattr(bot_instance, 'session_manager'):
                flash('Bot not initialized properly. Please try starting it again from dashboard.', 'danger')
                return redirect(url_for('index'))
        
        # Set verification code directly without creating a new event loop
        success = bot_instance.session_manager.set_verification_code(code)
        
        if success:
            flash('Verification code submitted. Processing...', 'info')
            # Wait a moment to process the verification
            time.sleep(2)
            
            # Check authentication status
            auth_status = bot_instance.session_manager.get_auth_status()
            
            if auth_status.get('requires_password', False):
                # Need 2FA password
                session['needs_2fa'] = True
                flash('Two-factor authentication is required. Please enter your 2FA password.', 'info')
                return redirect(url_for('twofa_password'))
            elif auth_status.get('status') == 'authorized':
                # Successfully authenticated
                flash('Authentication successful!', 'success')
                session.pop('needs_verification', None)
            elif auth_status.get('status') == 'error':
                # Authentication error
                flash(f'Authentication error: {auth_status.get("error")}', 'danger')
        else:
            flash('Error submitting verification code.', 'danger')
        
        return redirect(url_for('index'))
    
    # Check bot status for display
    bot_status_msg = None
    if bot_instance and hasattr(bot_instance, 'session_manager'):
        bot_status_msg = "Bot is running and waiting for verification"
    else:
        bot_status_msg = "Bot is not running properly"
    
    return render_template('verification_code.html', bot_status=bot_status_msg)

@app.route('/twofa-password', methods=['GET', 'POST'])
def twofa_password():
    """Handle Telegram 2FA password entry"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Make sure bot is running
    global bot_instance, bot_running
    if not bot_instance:
        logger.info("Starting bot for 2FA authentication")
        success = start_bot_thread()
        if not success:
            return render_template('2fa_password.html', error='Failed to start bot. Check your API settings.')
    
    if request.method == 'POST':
        password = request.form.get('password')
        if not password:
            return render_template('2fa_password.html', error='2FA password is required')
        
        if not bot_instance or not hasattr(bot_instance, 'session_manager'):
            return render_template('2fa_password.html', 
                                  error='Bot not initialized. Please go to the dashboard and start the bot first.')
        
        # Set flag for web authentication
        bot_instance.session_manager.auth_web_mode = True
        
        # Set 2FA password directly without creating a new event loop
        success = bot_instance.session_manager.set_2fa_password(password)
        
        if success:
            flash('2FA password submitted. Processing...', 'info')
        else:
            flash('Error submitting 2FA password.', 'danger')
        
        return redirect(url_for('index'))
    
    # Check bot status for display
    bot_status_msg = None
    if bot_instance and hasattr(bot_instance, 'session_manager'):
        bot_status_msg = "Bot is running and waiting for 2FA password"
    else:
        bot_status_msg = "Bot is not running properly"
    
    return render_template('2fa_password.html', bot_status=bot_status_msg)

@app.route('/bot/auth', methods=['POST'])
def bot_auth():
    """Handle Telegram authentication"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'})
    
    if not bot_instance or not hasattr(bot_instance, 'session_manager'):
        return jsonify({'success': False, 'message': 'Bot not initialized'})
    
    auth_type = request.form.get('auth_type')
    
    if auth_type == 'verification_code':
        code = request.form.get('code')
        if not code:
            return jsonify({'success': False, 'message': 'Verification code required'})
        
        # Set flag for web authentication
        bot_instance.session_manager.auth_web_mode = True
        
        # Set verification code directly without creating a new event loop
        success = bot_instance.session_manager.set_verification_code(code)
        
        if success:
            return jsonify({'success': True, 'message': 'Verification code submitted'})
        else:
            return jsonify({'success': False, 'message': 'Error submitting verification code'})
    
    elif auth_type == 'password':
        password = request.form.get('password')
        if not password:
            return jsonify({'success': False, 'message': '2FA password required'})
        
        # Set 2FA password directly without creating a new event loop
        success = bot_instance.session_manager.set_2fa_password(password)
        
        if success:
            return jsonify({'success': True, 'message': '2FA password submitted'})
        else:
            return jsonify({'success': False, 'message': 'Error submitting 2FA password'})
    
    return jsonify({'success': False, 'message': 'Invalid authentication type'})

@app.route('/admin/users', methods=['GET', 'POST'])
def admin_users():
    """User management for admins"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Admin access required', 'danger')
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add':
            username = request.form.get('username')
            password = request.form.get('password')
            is_admin = request.form.get('is_admin') == 'on'
            
            if User.query.filter_by(username=username).first():
                flash(f'User {username} already exists', 'danger')
            else:
                user = User(username=username, is_admin=is_admin)
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                flash(f'User {username} created successfully', 'success')
        
        elif action == 'delete':
            user_id = request.form.get('user_id')
            user = User.query.get(user_id)
            
            if user:
                db.session.delete(user)
                db.session.commit()
                flash(f'User {user.username} deleted successfully', 'success')
    
    users = User.query.all()
    return render_template('admin_users.html', users=users)

@app.route('/admin/logs')
def admin_logs():
    """View bot logs"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    page = request.args.get('page', 1, type=int)
    logs_per_page = 50
    
    logs = BotLog.query.order_by(BotLog.timestamp.desc()).paginate(page=page, per_page=logs_per_page)
    
    return render_template(
        'admin_logs.html',
        logs=logs
    )

# Create database and initial admin user if it doesn't exist
def create_initial_data():
    with app.app_context():
        db.create_all()
        
        # Create admin user if no users exist
        if User.query.count() == 0:
            admin = User(username='admin', is_admin=True)
            admin.set_password('admin')  # Default password, should be changed
            db.session.add(admin)
            db.session.commit()
            logger.info("Created initial admin user")

# Add database log handler to the logger
def setup_db_logging():
    db_handler = DatabaseLogHandler()
    db_handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(db_handler)
    logging.getLogger('telegram_forwarder.bot').addHandler(db_handler)
    logging.getLogger('telegram_forwarder.session').addHandler(db_handler)
    logging.getLogger('telegram_forwarder.message_handler').addHandler(db_handler)
    logging.getLogger('telegram_forwarder.text_filter').addHandler(db_handler)
    logging.getLogger('telegram_forwarder.media_processor').addHandler(db_handler)

# Command line entry point
def main():
    """Run the bot directly from command line"""
    logger.info("Starting Telegram Forwarder in CLI mode")
    
    # Load configuration
    config = get_config()
    try:
        if not config.sections():
            logger.error("Configuration file not found or empty. Please set up config.ini")
            return
    except Exception as e:
        logger.error(f"Error reading configuration: {e}")
        return
    
    # Create and run the bot
    try:
        bot = TelegramForwarderBot(config)
        logger.info("Bot initialized. Running...")
        bot.run()
    except KeyboardInterrupt:
        logger.info("Process interrupted by user. Shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return

if __name__ == "__main__":
    # If run directly, start the bot in CLI mode
    main()
else:
    # If imported as a module (by Flask), set up the web app
    # but don't start the bot automatically
    create_initial_data()
    setup_db_logging()
    # Make sure the bot doesn't run automatically when the module is imported
    # The bot will only start when users click "Start Bot" in the UI
