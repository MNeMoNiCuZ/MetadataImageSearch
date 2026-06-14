import os
import sys
import configparser
from typing import Any, Dict, Optional


def _get_config_path():
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller exe — place config.ini next to the exe
        return os.path.join(os.path.dirname(sys.executable), 'config.ini')
    else:
        # File lives at src/config/; go up to src/ then to root/
        src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        root_dir = os.path.dirname(src_dir)
        return os.path.join(root_dir, 'config.ini')


class ConfigManagerMetadataSearch:
    def __init__(self):
        self.config = configparser.ConfigParser()
        self.config_file = _get_config_path()
        
        # Create default sections if they don't exist
        if not os.path.exists(self.config_file):
            self.config['Interface'] = {
                'language': 'English'
            }
            self.config['Search'] = {
                'recursive': 'True',
                'case_sensitive': 'False',
                'search_positive': 'True',
                'search_negative': 'False'
            }
            self.config['Output'] = {
                'match_folder_structure': 'True',
                'create_or_subfolders': 'False',
                'enable_logging': 'False'
            }
            self.config['Paths'] = {
                'default_search_folder': '',
                'default_copy_folder': '',
                'default_move_folder': ''
            }
            self.save_config()
        else:
            self.config.read(self.config_file, encoding='utf-8')
    
    def get(self, section, key, default=None):
        """Get a value from the config"""
        try:
            return self.config.get(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default
    
    def get_bool(self, section, key, default=False):
        """Get a boolean value from the config"""
        try:
            return self.config.getboolean(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default
    
    def set(self, section, key, value):
        """Set a value in the config"""
        if not self.config.has_section(section):
            self.config.add_section(section)
        self.config.set(section, key, str(value))
    
    def save_config(self):
        """Save the current configuration to the config file"""
        with open(self.config_file, 'w', encoding='utf-8') as f:
            self.config.write(f)
    
    def get_all_settings(self) -> Dict[str, Dict[str, str]]:
        """Get all settings as a dictionary"""
        return {section: dict(self.config[section]) for section in self.config.sections()} 