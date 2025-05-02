import logging
import re
import datetime
import random
import string
import json
from html import escape as html_escape

class TextFilter:
    """
    Filters and transforms message text according to configured rules.
    Includes advanced formatting options and template variables.
    """
    def __init__(self, config):
        self.logger = logging.getLogger('telegram_forwarder.text_filter')
        self.config = config
        
        # Check if text filtering is enabled
        self.enabled = config.getboolean('TextFilters', 'enabled', fallback=True)
        
        # Load blacklist keywords
        self.blacklist_keywords = []
        if config.has_option('TextFilters', 'blacklist_keywords'):
            keywords = config['TextFilters']['blacklist_keywords']
            if keywords:
                self.blacklist_keywords = [kw.strip().lower() for kw in keywords.split(',')]
        
        # Load text replacements
        self.replacements = {}
        if config.has_option('TextFilters', 'replacements'):
            replacements = config['TextFilters']['replacements']
            if replacements:
                for replacement in replacements.split(','):
                    if ':' in replacement:
                        original, new = replacement.split(':', 1)
                        self.replacements[original.strip()] = new.strip()
        
        # Load regex patterns
        self.regex_enabled = config.getboolean('TextFilters', 'regex_enabled', fallback=False)
        self.regex_patterns = []
        if self.regex_enabled and config.has_option('TextFilters', 'regex_patterns'):
            patterns = config['TextFilters']['regex_patterns']
            if patterns:
                for pattern_pair in patterns.split(','):
                    if ':' in pattern_pair:
                        pattern, replacement = pattern_pair.split(':', 1)
                        try:
                            compiled_pattern = re.compile(pattern.strip())
                            self.regex_patterns.append((compiled_pattern, replacement.strip()))
                        except re.error as e:
                            self.logger.error(f"Invalid regex pattern '{pattern}': {e}")
        
        # Advanced formatting options
        self.formatting_enabled = config.getboolean('AdvancedFormatting', 'enabled', fallback=False)
        
        # Message templates
        self.message_templates = {}
        if self.formatting_enabled and config.has_option('AdvancedFormatting', 'templates'):
            try:
                templates_str = config['AdvancedFormatting']['templates']
                if templates_str:
                    # Parse templates that might be in JSON format or simple key:value pairs
                    if templates_str.strip().startswith('{'):
                        try:
                            self.message_templates = json.loads(templates_str)
                        except json.JSONDecodeError:
                            self.logger.error("Failed to parse templates JSON. Using simple format instead.")
                            self._parse_simple_templates(templates_str)
                    else:
                        self._parse_simple_templates(templates_str)
            except Exception as e:
                self.logger.error(f"Error loading message templates: {e}")
        
        # Formatting rules
        self.formatting_rules = {}
        if self.formatting_enabled and config.has_option('AdvancedFormatting', 'formatting_rules'):
            try:
                rules_str = config['AdvancedFormatting']['formatting_rules']
                if rules_str:
                    # Try JSON format first, then simple key-value pairs
                    if rules_str.strip().startswith('{'):
                        try:
                            self.formatting_rules = json.loads(rules_str)
                        except json.JSONDecodeError:
                            self.logger.error("Failed to parse formatting rules JSON. Using simple format instead.")
                            self._parse_simple_rules(rules_str)
                    else:
                        self._parse_simple_rules(rules_str)
            except Exception as e:
                self.logger.error(f"Error loading formatting rules: {e}")
        
        # Default template to use (if any)
        self.default_template = config['AdvancedFormatting']['default_template'] if (
            self.formatting_enabled and config.has_option('AdvancedFormatting', 'default_template')
        ) else None
        
        # Channel-specific templates
        self.channel_templates = {}
        if self.formatting_enabled and config.has_option('AdvancedFormatting', 'channel_templates'):
            try:
                channel_templates_str = config['AdvancedFormatting']['channel_templates']
                if channel_templates_str:
                    for mapping in channel_templates_str.split(','):
                        if ':' in mapping:
                            channel_id, template_name = mapping.split(':', 1)
                            self.channel_templates[channel_id.strip()] = template_name.strip()
            except Exception as e:
                self.logger.error(f"Error loading channel templates: {e}")
        
        # Message prefix and suffix
        self.message_prefix = config['AdvancedFormatting']['message_prefix'] if (
            self.formatting_enabled and config.has_option('AdvancedFormatting', 'message_prefix')
        ) else ""
        
        self.message_suffix = config['AdvancedFormatting']['message_suffix'] if (
            self.formatting_enabled and config.has_option('AdvancedFormatting', 'message_suffix')
        ) else ""
        
        # Format conversion rules (HTML to Markdown, etc.)
        self.convert_html_to_markdown = config.getboolean('AdvancedFormatting', 'convert_html_to_markdown', fallback=False)
        self.escape_html = config.getboolean('AdvancedFormatting', 'escape_html', fallback=False)
        
        # URL handling
        self.remove_urls = config.getboolean('AdvancedFormatting', 'remove_urls', fallback=False)
        self.replace_url_text = config['AdvancedFormatting']['replace_url_text'] if (
            self.formatting_enabled and config.has_option('AdvancedFormatting', 'replace_url_text')
        ) else "[URL]"
    
    def _parse_simple_templates(self, templates_str):
        """Parse simple key:value template strings"""
        for template in templates_str.split(','):
            if ':' in template:
                name, content = template.split(':', 1)
                self.message_templates[name.strip()] = content.strip()
    
    def _parse_simple_rules(self, rules_str):
        """Parse simple key:value formatting rule strings"""
        for rule in rules_str.split(','):
            if ':' in rule:
                trigger, format_type = rule.split(':', 1)
                self.formatting_rules[trigger.strip()] = format_type.strip()
    
    def process_text(self, text, source_channel=None, message_info=None):
        """
        Apply text filters and transformations with advanced formatting
        
        Args:
            text: The message text to process
            source_channel: Optional source channel ID for channel-specific templates
            message_info: Optional additional message information (e.g., author, date)
            
        Returns:
            str: The processed text or None if the message should be skipped
        """
        if not self.enabled or not text:
            return text
            
        # Check blacklist keywords
        if self.blacklist_keywords:
            text_lower = text.lower()
            for keyword in self.blacklist_keywords:
                if keyword in text_lower:
                    self.logger.debug(f"Message contains blacklisted keyword: {keyword}")
                    return None
        
        # Apply text replacements
        processed_text = text
        for original, replacement in self.replacements.items():
            processed_text = processed_text.replace(original, replacement)
        
        # Apply regex patterns
        if self.regex_enabled and self.regex_patterns:
            for pattern, replacement in self.regex_patterns:
                processed_text = pattern.sub(replacement, processed_text)
        
        # Apply advanced formatting if enabled
        if self.formatting_enabled:
            processed_text = self.apply_advanced_formatting(processed_text, source_channel, message_info)
        
        return processed_text
    
    def apply_advanced_formatting(self, text, source_channel=None, message_info=None):
        """
        Apply advanced formatting rules and templates to text
        
        Args:
            text: The text to format
            source_channel: Optional source channel ID
            message_info: Optional message metadata
            
        Returns:
            str: The formatted text
        """
        # Start with original text
        formatted_text = text
        
        # Apply URL handling
        if self.remove_urls:
            # Simple URL regex
            url_pattern = re.compile(r'https?://[^\s]+')
            formatted_text = url_pattern.sub(self.replace_url_text, formatted_text)
        
        # HTML handling
        if self.escape_html:
            formatted_text = html_escape(formatted_text)
        
        if self.convert_html_to_markdown:
            # Basic HTML to Markdown conversion for common tags
            # Convert <b>text</b> to **text**
            formatted_text = re.sub(r'<b>(.*?)</b>', r'**\1**', formatted_text)
            # Convert <i>text</i> to *text*
            formatted_text = re.sub(r'<i>(.*?)</i>', r'*\1*', formatted_text)
            # Convert <u>text</u> to __text__
            formatted_text = re.sub(r'<u>(.*?)</u>', r'__\1__', formatted_text)
            # Convert <s>text</s> to ~~text~~
            formatted_text = re.sub(r'<s>(.*?)</s>', r'~~\1~~', formatted_text)
            # Convert <code>text</code> to `text`
            formatted_text = re.sub(r'<code>(.*?)</code>', r'`\1`', formatted_text)
            # Convert <pre>text</pre> to ```text```
            formatted_text = re.sub(r'<pre>(.*?)</pre>', r'```\1```', formatted_text, flags=re.DOTALL)
            # Convert <a href="url">text</a> to [text](url)
            formatted_text = re.sub(r'<a href="(.*?)">(.*?)</a>', r'[\2](\1)', formatted_text)
        
        # Apply formatting rules if they exist
        for trigger, format_type in self.formatting_rules.items():
            if trigger in formatted_text:
                if format_type == "bold":
                    # Apply bold formatting to the whole text
                    formatted_text = f"**{formatted_text}**"
                elif format_type == "italic":
                    # Apply italic formatting to the whole text
                    formatted_text = f"*{formatted_text}*"
                elif format_type == "strikethrough":
                    # Apply strikethrough formatting to the whole text
                    formatted_text = f"~~{formatted_text}~~"
                elif format_type == "code":
                    # Apply code formatting to the whole text
                    formatted_text = f"`{formatted_text}`"
                elif format_type == "codeblock":
                    # Apply code block formatting to the whole text
                    formatted_text = f"```\n{formatted_text}\n```"
                elif format_type == "quote":
                    # Apply quote formatting - prefix each line with >
                    lines = formatted_text.split('\n')
                    formatted_text = '\n'.join([f"> {line}" for line in lines])
                elif format_type == "uppercase":
                    # Convert text to uppercase
                    formatted_text = formatted_text.upper()
                elif format_type == "lowercase":
                    # Convert text to lowercase
                    formatted_text = formatted_text.lower()
                elif format_type == "capitalize":
                    # Capitalize the first letter of each word
                    formatted_text = formatted_text.title()
        
        # Apply template if applicable
        template_name = None
        
        # Check for channel-specific template
        if source_channel and source_channel in self.channel_templates:
            template_name = self.channel_templates[source_channel]
        
        # Use default template if no channel-specific template
        if not template_name and self.default_template:
            template_name = self.default_template
            
        if template_name and template_name in self.message_templates:
            template = self.message_templates[template_name]
            
            # Create template variables
            template_vars = {
                'content': formatted_text,
                'date': datetime.datetime.now().strftime('%Y-%m-%d'),
                'time': datetime.datetime.now().strftime('%H:%M:%S'),
                'datetime': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'random_id': ''.join(random.choices(string.ascii_uppercase + string.digits, k=8)),
            }
            
            # Add message info if available
            if message_info:
                for key, value in message_info.items():
                    template_vars[key] = value
            
            # Apply template with variable substitution
            try:
                # Replace variables in format {variable_name}
                for var_name, var_value in template_vars.items():
                    placeholder = '{' + var_name + '}'
                    if placeholder in template:
                        template = template.replace(placeholder, str(var_value))
                        
                formatted_text = template
            except Exception as e:
                self.logger.error(f"Error applying template {template_name}: {e}")
        
        # Apply prefix and suffix
        if self.message_prefix:
            formatted_text = f"{self.message_prefix}\n{formatted_text}"
        
        if self.message_suffix:
            formatted_text = f"{formatted_text}\n{self.message_suffix}"
        
        return formatted_text
