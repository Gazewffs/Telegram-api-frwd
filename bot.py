import asyncio
import logging
import time
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, SessionPasswordNeededError, PhoneCodeInvalidError
from telethon.tl.types import Channel, User, Chat, MessageMediaPhoto, MessageMediaDocument

from session_manager import SessionManager
from message_handler import MessageHandler
from text_filter import TextFilter
from media_processor import MediaProcessor

class TelegramForwarderBot:
    """
    A bot that forwards messages from source channels to destination channels,
    with text filtering and media processing capabilities.
    """
    def __init__(self, config):
        self.logger = logging.getLogger('telegram_forwarder.bot')
        self.config = config
        
        # Initialize components
        self.session_manager = SessionManager(config)
        self.text_filter = TextFilter(config)
        self.media_processor = MediaProcessor(config)
        self.message_handler = MessageHandler(self.text_filter, self.media_processor, config)
        
        # Parse source and destination channels
        self.source_channels = self._parse_channels(config['Channels']['sources'])
        self.destination_channels = self._parse_channels(config['Channels']['destinations'])
        
        # Set advanced options
        self.check_interval = config.getint('Advanced', 'check_interval', fallback=1)
        self.max_message_age = config.getint('Advanced', 'max_message_age', fallback=24)
        self.batch_size = config.getint('Advanced', 'batch_size', fallback=50)
        self.parallel_destinations = config.getboolean('Advanced', 'parallel_destinations', fallback=True)
        
        self.client = None
        self.is_running = False
        self.last_processed_message_ids = {}
        
    def _parse_channels(self, channels_str):
        """Parse channel configuration string into a dictionary of channel_id: channel_name"""
        if not channels_str:
            return {}
            
        channels = {}
        for channel in channels_str.split(','):
            if ':' in channel:
                channel_id, channel_name = channel.strip().split(':', 1)
                try:
                    channels[int(channel_id)] = channel_name
                except ValueError:
                    self.logger.warning(f"Invalid channel ID: {channel_id}. Skipping.")
        return channels
        
    async def _forward_to_destination(self, message, destination_id, destination_name):
        """
        Helper method to forward a message to a specific destination
        Used for parallel processing of destinations
        """
        try:
            self.logger.info(f"Attempting to forward message {message.id} to {destination_name} ({destination_id})")
            result = await self.message_handler.process_and_forward(self.client, message, destination_id)
            if result:
                self.logger.info(f"Successfully forwarded message {message.id} to {destination_name}")
            else:
                self.logger.warning(f"Failed to forward message {message.id} to {destination_name}")
            return result
        except Exception as e:
            self.logger.error(f"Error forwarding message {message.id} to {destination_name}: {e}")
            return False
            
    async def _initialize_client(self):
        """Initialize and connect the Telegram client"""
        api_id = self.config['Auth']['api_id']
        api_hash = self.config['Auth']['api_hash']
        phone_number = self.config['Auth']['phone_number']
        session_path = self.config['Advanced']['session_path']
        
        if not api_id or not api_hash:
            raise ValueError("API ID and API Hash are required. Please check your config.ini file.")
            
        self.client = await self.session_manager.get_client(api_id, api_hash, phone_number, session_path)
        self.logger.info("Client initialized and connected")
        
        # Initialize last processed message ids for each source channel
        # Get the latest message ID for each channel instead of starting from 0
        # This ensures we only forward new messages after the bot starts
        for channel_id in self.source_channels:
            try:
                # Get the most recent message to set as our starting point
                source_name = self.source_channels[channel_id]
                self.logger.info(f"Getting latest message ID for {source_name} ({channel_id})")
                
                # Try to get the channel entity first to verify we can access it
                channel_entity = await self.client.get_entity(channel_id)
                self.logger.info(f"Successfully resolved channel entity: {channel_entity.title}")
                
                # Get the most recent message in the channel
                recent_messages = await self.client.get_messages(
                    channel_id,
                    limit=1  # Just get the most recent message
                )
                
                if recent_messages and len(recent_messages) > 0:
                    latest_id = recent_messages[0].id
                    self.last_processed_message_ids[channel_id] = latest_id
                    self.logger.info(f"Setting last processed message ID for {source_name} to {latest_id}")
                else:
                    self.logger.info(f"No messages found in {source_name}, setting last ID to 0")
                    self.last_processed_message_ids[channel_id] = 0
            except Exception as e:
                self.logger.error(f"Error initializing message ID for {channel_id}: {e}")
                self.last_processed_message_ids[channel_id] = 0
    
    async def _check_source_channel(self, source_id, source_name):
        """Check a single source channel for new messages"""
        try:
            self.logger.info(f"Now checking source channel: {source_name} ({source_id})")
            
            # Get the current time minus max_message_age
            time_threshold = datetime.now() - timedelta(hours=self.max_message_age)
            
            # Debug the channel
            try:
                channel_entity = await self.client.get_entity(source_id)
                self.logger.info(f"Successfully resolved channel entity: {channel_entity.title}")
            except Exception as entity_error:
                self.logger.error(f"Error resolving channel entity: {entity_error}")
                return  # Skip this channel
            
            # Get messages after the last processed one
            try:
                self.logger.info(f"Fetching messages from {source_name}, last processed ID: {self.last_processed_message_ids.get(source_id, 0)}")
                messages = await self.client.get_messages(
                    source_id,
                    limit=self.batch_size,  # Configurable batch size for better performance
                    min_id=self.last_processed_message_ids.get(source_id, 0)
                )
                self.logger.info(f"Successfully fetched messages: {len(messages)} messages found")
            except Exception as msg_error:
                self.logger.error(f"Error fetching messages: {msg_error}")
                return  # Skip this channel
            
            # Return messages and source info for processing
            return messages, source_id, source_name, time_threshold
        
        except FloodWaitError as e:
            self.logger.warning(f"Rate limit exceeded for {source_name}. Sleeping for {e.seconds} seconds")
            await asyncio.sleep(e.seconds)
            return None  # Skip this channel for now
        except Exception as e:
            self.logger.error(f"Error checking channel {source_name}: {e}")
            return None  # Skip this channel

    async def _check_source_channels(self):
        """Check source channels for new messages in parallel"""
        self.logger.info(f"Checking for new messages in {len(self.source_channels)} source channels")
        
        # Create tasks for checking each source channel
        check_tasks = []
        for source_id, source_name in self.source_channels.items():
            task = asyncio.create_task(self._check_source_channel(source_id, source_name))
            check_tasks.append(task)
        
        # Wait for all channel check tasks to complete
        check_results = await asyncio.gather(*check_tasks, return_exceptions=True)
        
        # Process results from each channel
        for result in check_results:
            # Skip any channels that had errors
            if not result or isinstance(result, Exception):
                continue
            
            try:
                messages, source_id, source_name, time_threshold = result
                
                # Process messages
                if messages:
                    self.logger.info(f"Found {len(messages)} new messages in {source_name}")
                    
                    # Sort by ID to process older messages first
                    messages.sort(key=lambda m: m.id)
                    
                    for msg in messages:
                        self.logger.info(f"Processing message {msg.id}")
                        
                        # Skip messages older than the threshold
                        if msg.date and msg.date.replace(tzinfo=None) < time_threshold:
                            self.logger.info(f"Skipping message {msg.id} - too old")
                            continue
                        
                        # Message content debug
                        self.logger.info(f"Message {msg.id} has text: {bool(msg.text)}, has media: {bool(msg.media)}")
                            
                        # Forward to all destination channels
                        if self.parallel_destinations and len(self.destination_channels) > 1:
                            # Process destination channels in parallel
                            self.logger.info(f"Forwarding message {msg.id} to {len(self.destination_channels)} destinations in parallel")
                            forwarding_tasks = []
                            
                            for dest_id, dest_name in self.destination_channels.items():
                                # Create a task for each destination
                                forward_task = asyncio.create_task(
                                    self._forward_to_destination(msg, dest_id, dest_name)
                                )
                                forwarding_tasks.append(forward_task)
                            
                            # Wait for all forwarding tasks to complete
                            if forwarding_tasks:
                                await asyncio.gather(*forwarding_tasks)
                        else:
                            # Process destination channels sequentially
                            for dest_id, dest_name in self.destination_channels.items():
                                try:
                                    self.logger.info(f"Attempting to forward message {msg.id} to {dest_name} ({dest_id})")
                                    result = await self.message_handler.process_and_forward(self.client, msg, dest_id)
                                    if result:
                                        self.logger.info(f"Successfully forwarded message {msg.id} to {dest_name}")
                                    else:
                                        self.logger.warning(f"Failed to forward message {msg.id} to {dest_name}")
                                    # Reduced sleep time for faster forwarding
                                    await asyncio.sleep(0.5)
                                except Exception as e:
                                    self.logger.error(f"Error forwarding message {msg.id} to {dest_name}: {e}")
                        
                        # Update last processed message ID
                        if msg.id > self.last_processed_message_ids.get(source_id, 0):
                            self.last_processed_message_ids[source_id] = msg.id
                            self.logger.info(f"Updated last processed message ID for {source_name} to {msg.id}")
                else:
                    self.logger.info(f"No new messages found in {source_name}")
            except Exception as e:
                self.logger.error(f"Error processing result for channel: {e}")
        
        # Sleep to avoid hitting rate limits
        await asyncio.sleep(self.check_interval)
    
    async def _run_loop(self):
        """Main processing loop for the bot"""
        self.logger.info("Starting forwarding loop")
        self.is_running = True
        
        while self.is_running:
            try:
                await self._check_source_channels()
            except KeyboardInterrupt:
                self.is_running = False
                self.logger.info("Bot stopped by user")
                break
            except Exception as e:
                self.logger.error(f"Error in main loop: {e}")
                # Sleep a bit longer after an error
                await asyncio.sleep(10)
    
    def run(self):
        """Run the bot"""
        # Create a new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Initialize and run the bot
            loop.run_until_complete(self._initialize_client())
            loop.run_until_complete(self._run_loop())
        except KeyboardInterrupt:
            self.logger.info("Bot stopped by user")
        except Exception as e:
            self.logger.error(f"Error running bot: {e}")
        finally:
            # Close client session
            if self.client:
                try:
                    loop.run_until_complete(self.client.disconnect())
                except Exception as e:
                    self.logger.error(f"Error disconnecting client: {e}")
            
            # Clean up
            loop.close()
            self.logger.info("Bot stopped")
