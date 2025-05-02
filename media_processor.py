import logging
import io
import os
import hashlib
import tempfile
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument

class MediaProcessor:
    """
    Processes media files in messages (photos, videos, documents).
    Handles image replacement and media processing.
    """
    def __init__(self, config):
        self.logger = logging.getLogger('telegram_forwarder.media_processor')
        self.config = config
        
        # Check if media processing is enabled
        self.enabled = config.getboolean('Media', 'media_enabled', fallback=True)
        
        # Replace all images setting
        self.replace_all_images = config.getboolean('Media', 'replace_all_images', fallback=False)
        
        # Default replacement image
        self.replacement_image_path = None
        if self.replace_all_images and config.has_option('Media', 'replacement_image_path'):
            path = config['Media']['replacement_image_path']
            if path and os.path.exists(path):
                self.replacement_image_path = path
            else:
                self.logger.warning(f"Replacement image path not found: {path}")
        
        # Path for the Billionaire AI BOT image to replace images with captions
        # Check possible locations for the image - this helps with different deployment environments
        possible_paths = [
            "static/images/billionaire_bot.png",  # Development path
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "static/images/billionaire_bot.png"),  # Module relative path
            os.environ.get("REPLACEMENT_IMAGE_PATH", ""),  # Environment variable override
            "/app/static/images/billionaire_bot.png"  # Render deployment path
        ]
        
        self.billionaire_bot_image_path = None
        for path in possible_paths:
            if path and os.path.exists(path):
                self.billionaire_bot_image_path = path
                self.logger.info(f"Billionaire AI BOT image loaded from {path}")
                break
        
        if not self.billionaire_bot_image_path:
            self.logger.warning("Billionaire AI BOT image not found in any of the search paths. Image replacement will be disabled.")
        
        # Load specific image replacements
        self.image_replacements = {}
        if config.has_option('Media', 'image_replacements'):
            replacements = config['Media']['image_replacements']
            if replacements:
                for replacement in replacements.split(','):
                    if ':' in replacement:
                        img_hash, repl_path = replacement.split(':', 1)
                        if os.path.exists(repl_path.strip()):
                            self.image_replacements[img_hash.strip()] = repl_path.strip()
                        else:
                            self.logger.warning(f"Replacement image not found: {repl_path.strip()}")
                            
    async def calculate_media_hash(self, client, message):
        """
        Calculate a hash for media to identify it for replacement
        
        Args:
            client: TelegramClient instance
            message: The message containing media
            
        Returns:
            str: The media hash or None if not applicable
        """
        try:
            # Download the media
            media_data = io.BytesIO()
            await client.download_media(message, file=media_data)
            
            # Calculate hash
            media_data.seek(0)
            media_hash = hashlib.md5(media_data.read()).hexdigest()
            
            return media_hash
        except Exception as e:
            self.logger.error(f"Error calculating media hash: {e}")
            return None
            
    async def process_media(self, client, message):
        """
        Process media in a message, applying replacements if needed
        
        Args:
            client: TelegramClient instance
            message: The message containing media
            
        Returns:
            dict: A dictionary with media information including file, type, and attributes
        """
        if not self.enabled or not message.media:
            return None
            
        try:
            # Create a result dictionary to store all necessary info
            result = {
                'file': None,
                'type': None,
                'filename': None,
                'mime_type': None,
                'attributes': {}
            }
            
            # Set the media type
            if isinstance(message.media, MessageMediaPhoto):
                result['type'] = 'photo'
            elif isinstance(message.media, MessageMediaDocument):
                result['type'] = 'document'
                # Try to determine document type
                if hasattr(message.media.document, 'mime_type'):
                    mime = message.media.document.mime_type
                    result['mime_type'] = mime
                    
                    if mime.startswith('image/'):
                        result['type'] = 'photo'  # Treat as photo for better display
                    elif mime.startswith('video/'):
                        result['type'] = 'video'
                    elif mime.startswith('audio/'):
                        result['type'] = 'audio'
                
                # Extract document attributes
                if hasattr(message.media.document, 'attributes'):
                    for attr in message.media.document.attributes:
                        if hasattr(attr, 'file_name'):
                            result['filename'] = attr.file_name
                        
                        # Store any other useful attributes
                        for key in dir(attr):
                            if not key.startswith('_') and not callable(getattr(attr, key)):
                                result['attributes'][key] = getattr(attr, key)
            
            # Check if the message has a caption and if it's an image
            has_caption = False
            caption_text = None
            
            # Try various ways Telethon might store captions
            if hasattr(message, 'caption') and message.caption:
                has_caption = True
                caption_text = message.caption
                self.logger.debug(f"Found caption in message.caption: {caption_text}")
            elif hasattr(message, 'message') and message.message:
                has_caption = True
                caption_text = message.message
                self.logger.debug(f"Found caption in message.message: {caption_text}")
                
            # Check if we should replace this image with the Billionaire BOT image
            if has_caption and (
                isinstance(message.media, MessageMediaPhoto) or 
                (isinstance(message.media, MessageMediaDocument) and 
                 hasattr(message.media.document, 'mime_type') and 
                 message.media.document.mime_type.startswith('image/'))
            ) and self.billionaire_bot_image_path:
                # Replace image with caption with Billionaire AI BOT image
                self.logger.info(f"Replacing image with caption: '{caption_text}' with Billionaire AI BOT image")
                result['file'] = self.billionaire_bot_image_path
                result['is_path'] = True
                return result
            
            # Check if we should replace this media
            if isinstance(message.media, MessageMediaPhoto) and self.replace_all_images:
                # Replace all images mode is enabled
                if self.replacement_image_path:
                    self.logger.debug("Replacing image with default replacement")
                    result['file'] = self.replacement_image_path
                    result['is_path'] = True
                    return result
                    
            # Check for specific image replacement
            if self.image_replacements and (
                isinstance(message.media, MessageMediaPhoto) or 
                (isinstance(message.media, MessageMediaDocument) and 
                 hasattr(message.media.document, 'mime_type') and 
                 message.media.document.mime_type.startswith('image/'))
            ):
                # Calculate media hash
                media_hash = await self.calculate_media_hash(client, message)
                
                if media_hash and media_hash in self.image_replacements:
                    replacement_path = self.image_replacements[media_hash]
                    self.logger.debug(f"Replacing media with hash {media_hash}")
                    result['file'] = replacement_path
                    result['is_path'] = True
                    return result
            
            # Download directly using Telethon's original download functionality
            # Use a temporary file for better compatibility with Telegram's upload functions
            import tempfile
            temp_path = None
            
            # For photos, use direct download to get the best quality
            if isinstance(message.media, MessageMediaPhoto):
                temp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                temp.close()
                temp_path = temp.name
                await client.download_media(message, file=temp_path)
                result['file'] = temp_path
                result['is_path'] = True
                result['type'] = 'photo'
                self.logger.debug(f"Downloaded photo to temporary file: {temp_path}")
            # For documents, attempt to preserve mime type and filename
            else:
                try:
                    # First try to get attributes for better identification
                    suffix = ".bin"  # Default suffix if nothing else is found
                    if hasattr(message.media, 'document'):
                        doc = message.media.document
                        # Determine suffix from mime type
                        if hasattr(doc, 'mime_type'):
                            mime = doc.mime_type
                            if mime == 'image/jpeg':
                                suffix = '.jpg'
                            elif mime == 'image/png':
                                suffix = '.png'
                            elif mime == 'image/gif':
                                suffix = '.gif'
                            elif mime == 'video/mp4':
                                suffix = '.mp4'
                            elif mime == 'application/pdf':
                                suffix = '.pdf'
                            elif '/' in mime:
                                # Extract extension from mime type
                                suffix = '.' + mime.split('/')[1]
                        
                        # Check for filename in attributes
                        if hasattr(doc, 'attributes'):
                            for attr in doc.attributes:
                                if hasattr(attr, 'file_name') and attr.file_name:
                                    # Extract extension from filename
                                    if '.' in attr.file_name:
                                        suffix = '.' + attr.file_name.split('.')[-1]
                                    break
                    
                    # Use a temporary file with proper extension
                    temp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                    temp.close()
                    temp_path = temp.name
                    await client.download_media(message, file=temp_path)
                    result['file'] = temp_path
                    result['is_path'] = True
                    self.logger.debug(f"Downloaded document to temporary file: {temp_path}")
                except Exception as download_error:
                    self.logger.error(f"Error downloading to temp file: {download_error}")
                    # Fallback to in-memory file
                    media_file = io.BytesIO()
                    await client.download_media(message, file=media_file)
                    media_file.seek(0)
                    result['file'] = media_file
                    result['is_path'] = False
                    self.logger.debug("Downloaded document to memory buffer (fallback)")
            
            self.logger.debug(f"Processed media of type {result['type']}, " + 
                              f"filename: {result['filename']}, mime: {result['mime_type']}")
            
            return result
                
        except Exception as e:
            self.logger.error(f"Error processing media: {e}")
            return None
