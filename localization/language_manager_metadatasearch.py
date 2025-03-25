import os
import json
from typing import Dict, Optional

class LanguageManagerMetadataSearch:
    def __init__(self, base_name: str, initial_language: str = "English"):
        """Initialize the language manager with a base name for language files and initial language"""
        self.base_name = base_name
        self.strings = {}
        self.tooltips = {}
        self.current_language = None  # Will be set after loading languages
        self.language_codes = {}
        self._load_languages()
        
        # Set initial language, defaulting to English if specified language not found
        if initial_language in self.get_languages():
            self.set_language(initial_language)
        else:
            self.set_language("English")

    def _load_languages(self):
        """Load available language files and their codes"""
        localization_dir = os.path.dirname(os.path.abspath(__file__))
        for file in os.listdir(localization_dir):
            if file.startswith(f"{self.base_name}-") and file.endswith(".json"):
                lang_code = file[len(f"{self.base_name}-"):-5]  # Extract language code from filename
                file_path = os.path.join(localization_dir, file)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if "language" in data and "name" in data["language"]:
                            lang_name = data["language"]["name"]
                            self.language_codes[lang_name] = lang_code
                except UnicodeDecodeError:
                    # Try with different encodings if UTF-8 fails
                    for encoding in ['utf-8-sig', 'utf-16', 'utf-16le', 'utf-16be']:
                        try:
                            with open(file_path, 'r', encoding=encoding) as f:
                                data = json.load(f)
                                if "language" in data and "name" in data["language"]:
                                    lang_name = data["language"]["name"]
                                    self.language_codes[lang_name] = lang_code
                                    break
                        except UnicodeDecodeError:
                            continue

    def set_language(self, language: str):
        """Set the current language and load its strings"""
        if language in self.get_languages():
            self.current_language = language
            lang_code = self.language_codes.get(language, language)
            lang_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   f"{self.base_name}-{lang_code}.json")
            
            if os.path.exists(lang_file):
                try:
                    with open(lang_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        # Store all data except tooltips as strings
                        self.strings = {k: v for k, v in data.items() if k != "tooltips"}
                        self.tooltips = data.get("tooltips", {})
                    return True
                except UnicodeDecodeError:
                    # Try with different encodings if UTF-8 fails
                    for encoding in ['utf-8-sig', 'utf-16', 'utf-16le', 'utf-16be']:
                        try:
                            with open(lang_file, 'r', encoding=encoding) as f:
                                data = json.load(f)
                                self.strings = {k: v for k, v in data.items() if k != "tooltips"}
                                self.tooltips = data.get("tooltips", {})
                                return True
                        except UnicodeDecodeError:
                            continue
        return False

    def get_languages(self) -> list:
        """Get list of available languages"""
        return list(self.language_codes.keys())

    def get_string(self, key: str, *args) -> str:
        """Get a localized string by key with optional format arguments"""
        # Split the key by dots to traverse nested dictionaries
        keys = key.split('.')
        value = self.strings
        
        # Traverse the nested structure
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                # Key not found, return the key itself as fallback
                return key
        
        # If we found a dict instead of a string, check for 'text' key or return original key
        if isinstance(value, dict):
            if 'text' in value:
                value = value['text']
            else:
                return key
        
        # Format the string if arguments are provided
        if args:
            try:
                return value.format(*args)
            except (IndexError, KeyError):
                return value
        
        return value if isinstance(value, str) else key

    def get_tooltip(self, key: str) -> str:
        """Get a localized tooltip by key"""
        # First check in tooltips section
        tooltip = self.tooltips.get(key, "")
        if tooltip:
            if isinstance(tooltip, dict) and "text" in tooltip:
                tooltip = tooltip["text"]
            return tooltip if isinstance(tooltip, str) else ""
            
        # Then check if it's a checkbox with a tooltip
        checkbox_key = f"checkboxes.{key}.tooltip"
        value = self.get_string(checkbox_key)
        if value != checkbox_key:  # If we got a real value back
            return value
            
        return ""

    def get_language_code(self, language_name: str) -> str:
        """Get the language code for a language name"""
        return self.language_codes.get(language_name, language_name)

    def get_language_name(self, language_code: str) -> str:
        """Get the language name for a language code"""
        for name, code in self.language_codes.items():
            if code == language_code:
                return name
        return language_code

    def _get_nested_value(self, key: str) -> Optional[str]:
        """Get a value from nested dictionaries using dot notation"""
        keys = key.split('.')
        value = self.strings
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return None
        
        return value if isinstance(value, str) else None 