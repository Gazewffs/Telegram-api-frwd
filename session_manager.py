import logging
import os
import asyncio
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError

class SessionManager:
    """
    Manages Telegram sessions including authentication and client initialization.
    """
    def __init__(self, config):
        self.logger = logging.getLogger('telegram_forwarder.session')
        self.config = config
        self.auth_lock = asyncio.Lock()
        self.pending_auth = {}
        self.auth_web_mode = False  # Flag to determine if we're in web or CLI auth mode
        self.main_loop = None  # Store the main event loop
        self.verification_code = None  # Store verification code from web interface
        self.twofa_password = None  # Store 2FA password from web interface
        
    async def get_client(self, api_id, api_hash, phone_number, session_path):
        """
        Create and authenticate a TelegramClient instance
        
        Args:
            api_id: Telegram API ID
            api_hash: Telegram API Hash
            phone_number: User's phone number
            session_path: Path to save the session file
            
        Returns:
            TelegramClient: An authenticated TelegramClient instance
        """
        # Ensure session directory exists
        os.makedirs(os.path.dirname(session_path), exist_ok=True)
        
        # Save the current event loop to ensure we use the same one throughout
        self.main_loop = asyncio.get_event_loop()
        
        # Check if we have a session file already
        session_file_exists = os.path.exists(f"{session_path}.session")
        session_file_size = 0
        if session_file_exists:
            try:
                session_file_size = os.path.getsize(f"{session_path}.session")
                self.logger.info(f"Found existing session file (size: {session_file_size} bytes)")
            except Exception as e:
                self.logger.error(f"Error checking session file size: {e}")
        
        # Create client with specific connection settings for better reliability
        client = TelegramClient(
            session_path, 
            api_id, 
            api_hash,
            connection_retries=10,
            retry_delay=1,
            auto_reconnect=True
        )
        
        try:
            # Connect to Telegram with timeout
            await asyncio.wait_for(client.connect(), timeout=30)
            self.logger.info("Connected to Telegram servers")
            
            # If session file exists and is valid size but still not authorized,
            # it might be corrupted - handle this case
            if session_file_exists and session_file_size > 0:
                is_authorized = await client.is_user_authorized()
                if not is_authorized:
                    self.logger.warning("Session file exists but user is not authorized - might be corrupted")
                    # Disconnect and try to remove the session
                    await client.disconnect()
                    try:
                        session_file_path = f"{session_path}.session"
                        if os.path.exists(session_file_path):
                            os.remove(session_file_path)
                            self.logger.info(f"Removed corrupted session file: {session_file_path}")
                        
                        # Recreate client and connect
                        client = TelegramClient(session_path, api_id, api_hash)
                        await client.connect()
                        self.logger.info("Reconnected after removing corrupted session file")
                    except Exception as cleanup_error:
                        self.logger.error(f"Error cleaning up corrupted session: {cleanup_error}")
            
            # Check if already authorized
            if not await client.is_user_authorized():
                self.logger.info("User not authorized. Starting authorization process...")
                
                # Send code request
                await client.send_code_request(phone_number)
                self.logger.info(f"Verification code sent to {phone_number}")
                
                # Store auth information for web interface
                async with self.auth_lock:
                    self.pending_auth = {
                        'client': client,
                        'phone_number': phone_number,
                        'status': 'waiting_for_code',
                        'error': None,
                        'requires_password': False
                    }
                
                # Web mode or CLI mode
                if self.auth_web_mode:
                    # Web mode - wait for code to be set via set_verification_code
                    max_wait_time = 300  # 5 minutes
                    wait_interval = 2  # 2 seconds
                    total_waited = 0
                    
                    # Check for verification code or wait for it to be set via web interface
                    while (self.verification_code is None and 
                          self.pending_auth['status'] == 'waiting_for_code' and 
                          total_waited < max_wait_time):
                        await asyncio.sleep(wait_interval)
                        total_waited += wait_interval
                        
                        # If code has been set via web interface, use it
                        if self.verification_code:
                            code = self.verification_code
                            self.verification_code = None  # Reset after use
                            
                            try:
                                await client.sign_in(phone_number, code)
                                self.pending_auth['status'] = 'authorized'
                                self.logger.info("Successfully signed in with verification code")
                            except SessionPasswordNeededError:
                                # 2FA is enabled
                                self.logger.info("Two-factor authentication is enabled")
                                self.pending_auth['requires_password'] = True
                                self.pending_auth['status'] = 'waiting_for_password'
                                
                                # Now wait for 2FA password
                                while (self.twofa_password is None and 
                                      self.pending_auth['status'] == 'waiting_for_password' and 
                                      total_waited < max_wait_time):
                                    await asyncio.sleep(wait_interval)
                                    total_waited += wait_interval
                                    
                                    if self.twofa_password:
                                        password = self.twofa_password
                                        self.twofa_password = None  # Reset after use
                                        
                                        try:
                                            await client.sign_in(password=password)
                                            self.pending_auth['status'] = 'authorized'
                                            self.logger.info("Successfully signed in with 2FA password")
                                        except Exception as e:
                                            self.logger.error(f"Error submitting 2FA password: {e}")
                                            self.pending_auth['error'] = e
                                            self.pending_auth['status'] = 'error'
                                
                            except PhoneCodeInvalidError:
                                self.logger.error("Invalid verification code")
                                self.pending_auth['error'] = PhoneCodeInvalidError("Invalid verification code")
                                self.pending_auth['status'] = 'error'
                            except Exception as e:
                                self.logger.error(f"Error submitting verification code: {e}")
                                self.pending_auth['error'] = e
                                self.pending_auth['status'] = 'error'
                else:
                    # CLI mode - use standard input
                    try:
                        verification_code = input("Enter the verification code received: ")
                        await self.submit_verification_code(verification_code)
                    except Exception as e:
                        self.logger.error(f"Error during CLI authentication: {e}")
                        raise
                
                # Check final status
                if self.pending_auth['status'] == 'waiting_for_code' or self.pending_auth['status'] == 'waiting_for_password':
                    self.logger.error("Timed out waiting for authentication")
                    raise TimeoutError("Authentication timed out.")
                    
                if self.pending_auth['status'] == 'error':
                    error = self.pending_auth['error']
                    self.logger.error(f"Authentication error: {error}")
                    raise error
                    
                self.logger.info("Authorization successful")
            else:
                self.logger.info("User already authorized - using existing session")
        except Exception as e:
            self.logger.error(f"Error during authentication: {e}")
            
            # If this is a connection error, try one more time
            if "Connection" in str(e):
                try:
                    self.logger.info("Retrying connection after connection error...")
                    if not client.is_connected():
                        await client.connect()
                    
                    # If connected and authorized, we can proceed
                    if await client.is_user_authorized():
                        self.logger.info("Successfully connected and already authorized")
                        return client
                except Exception as retry_error:
                    self.logger.error(f"Error during connection retry: {retry_error}")
            
            # If all else fails and we have a session file, try to delete it
            # This will force re-authentication on the next attempt
            if session_file_exists:
                try:
                    # Disconnect the client first to release the session file
                    await client.disconnect()
                    session_file_path = f"{session_path}.session"
                    if os.path.exists(session_file_path):
                        os.remove(session_file_path)
                        self.logger.info(f"Removed potentially corrupted session file: {session_file_path}")
                    
                    # Reconnect and start over
                    client = TelegramClient(session_path, api_id, api_hash)
                    await client.connect()
                    self.logger.info("Reconnected after removing session file")
                    
                    # Try authentication again (will request new verification code)
                    await client.send_code_request(phone_number)
                    self.logger.info(f"New verification code sent to {phone_number}")
                    
                    # Restore auth state
                    async with self.auth_lock:
                        self.pending_auth = {
                            'client': client,
                            'phone_number': phone_number,
                            'status': 'waiting_for_code',
                            'error': None,
                            'requires_password': False
                        }
                        
                    # Raise exception to handle in the main loop
                    raise RuntimeError("Session corrupted, please try again with a new verification code")
                except Exception as cleanup_error:
                    self.logger.error(f"Error cleaning up session: {cleanup_error}")
                    raise RuntimeError("Unable to clean up corrupted session, please restart the application")
            else:
                raise
            
        return client
    
    async def submit_verification_code(self, code):
        """
        Submit the verification code received from Telegram
        
        Args:
            code: Verification code
            
        Returns:
            bool: True if successful, False otherwise
        """
        async with self.auth_lock:
            if not self.pending_auth or 'client' not in self.pending_auth:
                return False
                
            client = self.pending_auth['client']
            phone_number = self.pending_auth['phone_number']
            
            try:
                await client.sign_in(phone_number, code)
                self.pending_auth['status'] = 'authorized'
                return True
            except SessionPasswordNeededError:
                # 2FA is enabled
                self.logger.info("Two-factor authentication is enabled")
                self.pending_auth['requires_password'] = True
                self.pending_auth['status'] = 'waiting_for_password'
                return False
            except PhoneCodeInvalidError:
                self.logger.error("Invalid verification code")
                self.pending_auth['error'] = PhoneCodeInvalidError("Invalid verification code")
                self.pending_auth['status'] = 'error'
                return False
            except Exception as e:
                self.logger.error(f"Error submitting verification code: {e}")
                self.pending_auth['error'] = e
                self.pending_auth['status'] = 'error'
                return False
    
    async def submit_2fa_password(self, password):
        """
        Submit the 2FA password
        
        Args:
            password: 2FA password
            
        Returns:
            bool: True if successful, False otherwise
        """
        async with self.auth_lock:
            if not self.pending_auth or 'client' not in self.pending_auth:
                return False
                
            client = self.pending_auth['client']
            
            try:
                await client.sign_in(password=password)
                self.pending_auth['status'] = 'authorized'
                return True
            except Exception as e:
                self.logger.error(f"Error submitting 2FA password: {e}")
                self.pending_auth['error'] = e
                self.pending_auth['status'] = 'error'
                return False
                
    def get_auth_status(self):
        """Get current authentication status"""
        if not self.pending_auth:
            return {'status': 'none'}
            
        return {
            'status': self.pending_auth['status'],
            'requires_password': self.pending_auth.get('requires_password', False),
            'error': str(self.pending_auth['error']) if self.pending_auth.get('error') else None
        }
        
    def set_verification_code(self, code):
        """
        Set verification code without using async methods.
        This method is called from the web interface without creating a new event loop.
        
        Args:
            code: Verification code
            
        Returns:
            bool: True if set successfully
        """
        self.verification_code = code
        self.logger.info(f"Verification code set: {code[:1]}****")
        return True
        
    def set_2fa_password(self, password):
        """
        Set 2FA password without using async methods.
        This method is called from the web interface without creating a new event loop.
        
        Args:
            password: 2FA password
            
        Returns:
            bool: True if set successfully
        """
        self.twofa_password = password
        self.logger.info("2FA password set")
        return True
    
    async def end_session(self, client):
        """Safely disconnect the client"""
        if client:
            await client.disconnect()
            self.logger.info("Client disconnected")
