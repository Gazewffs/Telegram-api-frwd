import logging
import os
import io
from telethon.tl.types import (
    MessageEntityBold, MessageEntityItalic, MessageEntityCode,
    MessageEntityPre, MessageEntityTextUrl, MessageEntityMention,
    MessageEntityHashtag, MessageEntityCashtag, MessageEntityBotCommand,
    MessageEntityUrl, MessageEntityEmail, MessageEntityPhone,
    MessageMediaPhoto, MessageMediaDocument
)

class MessageHandler:
    """
    Handles message processing, filtering, and forwarding.
    """
    def __init__(self, text_filter, media_processor, config):
        self.logger = logging.getLogger('telegram_forwarder.message_handler')
        self.text_filter = text_filter
        self.media_processor = media_processor
        self.config = config
        
        # Settings
        self.preserve_formatting = config.getboolean('Advanced', 'preserve_formatting', fallback=True)
        self.copy_media = config.getboolean('Advanced', 'copy_media', fallback=True)
        
    async def process_and_forward(self, client, message, destination_id):
        """
        Process a message and forward it to the destination channel
        
        Args:
            client: TelegramClient instance
            message: The message to process
            destination_id: ID of the destination channel
            
        Returns:
            bool: True if forwarded successfully, False otherwise
        """
        # Debug to understand the message structure
        self.logger.info(f"Message attributes: {dir(message)}")
        
        # Check for various caption attributes that might be used by Telethon
        caption = None
        if hasattr(message, 'caption'):
            caption = message.caption
            self.logger.info(f"Found message.caption: {caption}")
        elif hasattr(message, 'message') and message.message:
            caption = message.message
            self.logger.info(f"Found message.message as caption: {caption}")
            
        # Skip empty messages
        if not message.text and not message.media and not caption:
            self.logger.debug("Skipping empty message")
            return False
            
        try:
            # Process text - prioritize caption for media messages
            if caption and message.media:
                # Media with caption - use the caption as the text
                filtered_text = self.text_filter.process_text(caption)
                self.logger.info(f"Using caption as text: {caption} -> {filtered_text}")
            elif message.text:
                # Text message without media or media without caption
                filtered_text = self.text_filter.process_text(message.text)
                self.logger.info(f"Using message.text: {message.text} -> {filtered_text}")
            else:
                filtered_text = ""
                
            # If the text was filtered out completely, skip unless there's media
            if not filtered_text and not message.media:
                self.logger.debug("Message text filtered out completely")
                return False
                
            # If we have a blacklist match, skip the message
            if filtered_text is None:
                self.logger.debug("Message matched blacklist filter")
                return False
                
            # Process media
            media_info = None
            if message.media and self.copy_media:
                # Download and process media
                media_info = await self.media_processor.process_media(client, message)
                
            # Create a new message in the destination channel
            if media_info and media_info.get('file'):
                file_to_send = media_info['file']
                media_type = media_info.get('type', 'document')
                filename = media_info.get('filename')
                mime_type = media_info.get('mime_type')
                is_path = media_info.get('is_path', False)
                
                self.logger.debug(f"Sending media: type={media_type}, filename={filename}, mime={mime_type}")
                
                # Force attributes for better mobile display
                force_document = False
                if media_type == 'document' and not mime_type:
                    force_document = True
                elif media_type in ['video', 'audio', 'gif']:
                    force_document = False
                    
                try:
                    # Send with proper attributes
                    await client.send_file(
                        destination_id,
                        file_to_send,
                        caption=filtered_text,
                        parse_mode='md' if self.preserve_formatting else None,
                        filename=filename,
                        force_document=force_document,
                        attributes=None,
                        thumb=None
                    )
                    self.logger.debug(f"Successfully sent media with type: {media_type}")
                except Exception as send_error:
                    self.logger.error(f"Error sending media: {send_error}")
                    
                    # Try a fallback method of sending just as a plain document
                    try:
                        self.logger.debug("Trying fallback method of sending media")
                        await client.send_file(
                            destination_id,
                            file_to_send,
                            caption=filtered_text,
                            parse_mode='md' if self.preserve_formatting else None,
                            force_document=True  # Force as document in fallback
                        )
                        self.logger.debug("Successfully sent media using fallback method")
                    except Exception as fallback_error:
                        self.logger.error(f"Fallback also failed: {fallback_error}")
                        # If all else fails, just send the text
                        if filtered_text:
                            await client.send_message(
                                destination_id,
                                filtered_text,
                                parse_mode='md' if self.preserve_formatting else None
                            )
            else:
                # Send text only
                await client.send_message(
                    destination_id,
                    filtered_text,
                    parse_mode='md' if self.preserve_formatting else None
                )
                
            return True
                
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")
            return False
