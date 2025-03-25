import os
import sys
import argparse
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
from PIL import Image
import re
from datetime import datetime
import shutil
import fnmatch
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from tqdm import tqdm
import queue
from threading import Lock
from functools import partial
import multiprocessing
from localization.language_manager_metadatasearch import LanguageManagerMetadataSearch
from config.config_manager_metadatasearch import ConfigManagerMetadataSearch

# Function to process a single image file (needs to be at module level for multiprocessing)
def process_single_image(args):
    image_path, search_term, search_positive, search_negative, case_sensitive, custom_filter, ignore_term = args
    try:
        with Image.open(image_path) as image:
            exif_data = image.info
            if not exif_data:
                return None
            
            metadata = parse_exif_data(exif_data)
            match_result = matches_search_term(metadata, search_term, search_positive, search_negative, case_sensitive, ignore_term)
            if match_result and apply_custom_filter(image_path, metadata, custom_filter):
                return (image_path, match_result)
    except Exception:
        return None
    return None

def parse_exif_data(exif_data):
    if not exif_data or 'parameters' not in exif_data:
        return {}
    
    params = exif_data['parameters']
    parsed_data = {}
    
    # Extract positive prompt (everything before "Negative prompt:")
    positive_end = params.find('Negative prompt:')
    if positive_end != -1:
        parsed_data['Positive'] = params[:positive_end].strip()
        
        # Extract negative prompt (between "Negative prompt:" and "Steps:")
        negative_start = positive_end + len('Negative prompt:')
        negative_end = params.find('Steps:')
        if negative_end != -1:
            parsed_data['Negative'] = params[negative_start:negative_end].strip()
    
    # Extract other parameters
    param_patterns = {
        'Steps': r'Steps: (.*?)(?:,|$)',
        'Sampler': r'Sampler: (.*?)(?:,|$)',
        'CFG scale': r'CFG scale: (.*?)(?:,|$)',
        'Seed': r'Seed: (.*?)(?:,|$)',
        'Size': r'Size: (.*?)(?:,|$)',
        'Model': r'Model: (.*?)(?:,|$)',
        'Denoising strength': r'Denoising strength: (.*?)(?:,|$)',
        'Clip skip': r'Clip skip: (.*?)(?:,|$)',
        'Hires upscale': r'Hires upscale: (.*?)(?:,|$)',
        'Hires steps': r'Hires steps: (.*?)(?:,|$)',
        'Hires upscaler': r'Hires upscaler: (.*?)(?:,|$)',
        'Lora hashes': r'Lora hashes: "(.*?)"(?:,|$)'
    }
    
    for key, pattern in param_patterns.items():
        match = re.search(pattern, params)
        if match:
            parsed_data[key] = match.group(1).strip()
    
    return parsed_data

def matches_search_term(metadata, search_term, search_positive, search_negative, case_sensitive, ignore_term=None):
    if not metadata:
        return None
        
    # If search term is empty, consider it no match
    if not search_term:
        return None
        
    # First check if any ignore terms match
    if ignore_term:
        # Split ignore terms by OR first, then by AND
        ignore_or_terms = [term.strip() for term in ignore_term.split('||')]
        
        # For each OR group in ignore terms
        for ignore_group in ignore_or_terms:
            if not ignore_group:  # Skip empty terms
                continue
                
            # Split by AND
            ignore_and_terms = [term.strip() for term in ignore_group.split('&&')]
            all_and_match = True
            
            # Check if all AND terms match
            for term in ignore_and_terms:
                if not term:  # Skip empty terms
                    continue
                    
                # Convert wildcards to regex pattern
                pattern = re.escape(term).replace(r'\*', '.*').replace(r'\?', '.')
                pattern = f'.*{pattern}.*'
                
                # Try to match the term
                term_matched = False
                
                # Check if term should be ignored
                if search_positive or search_negative:
                    if search_positive and 'Positive' in metadata:
                        text = metadata['Positive']
                        if re.search(pattern, text, flags=0 if case_sensitive else re.IGNORECASE):
                            term_matched = True
                    if search_negative and 'Negative' in metadata:
                        text = metadata['Negative']
                        if re.search(pattern, text, flags=0 if case_sensitive else re.IGNORECASE):
                            term_matched = True
                else:
                    # Search all metadata
                    for key, value in metadata.items():
                        if isinstance(value, str):
                            if re.search(pattern, value, flags=0 if case_sensitive else re.IGNORECASE):
                                term_matched = True
                                break
                
                if not term_matched:
                    all_and_match = False
                    break
            
            # If all AND terms matched in this OR group, ignore the file
            if all_and_match:
                return None
    
    # Split search terms by OR (||) first, then by AND (&&)
    or_terms = [term.strip() for term in search_term.split('||')]
    
    # For each OR group
    for or_index, or_group in enumerate(or_terms):
        # Skip empty OR terms (shouldn't happen after validation, but just in case)
        if not or_group:
            continue
            
        # Split by AND
        and_terms = [term.strip() for term in or_group.split('&&')]
        
        # Check if all AND terms match
        all_and_match = True
        for term in and_terms:
            if not term:  # Skip empty terms
                continue
                
            # Convert wildcards to regex pattern
            pattern = re.escape(term).replace(r'\*', '.*').replace(r'\?', '.')
            # Allow partial matches within the text
            pattern = f'.*{pattern}.*'
            
            # Try to match the term
            term_matched = False
            
            # Only search in specified prompts if options are set
            if search_positive or search_negative:
                if search_positive and 'Positive' in metadata:
                    text = metadata['Positive']
                    if re.search(pattern, text, flags=0 if case_sensitive else re.IGNORECASE):
                        term_matched = True
                if search_negative and 'Negative' in metadata:
                    text = metadata['Negative']
                    if re.search(pattern, text, flags=0 if case_sensitive else re.IGNORECASE):
                        term_matched = True
            else:
                # Search all metadata
                for key, value in metadata.items():
                    if isinstance(value, str):
                        if re.search(pattern, value, flags=0 if case_sensitive else re.IGNORECASE):
                            term_matched = True
                            break
            
            if not term_matched:
                all_and_match = False
                break
        
        # If all AND terms matched, we have a match
        if all_and_match:
            return (or_index, or_group)
    
    return None

def apply_custom_filter(image_path, metadata, custom_filter):
    if not custom_filter:
        return True
        
    # Apply regex filter
    filter_text = custom_filter.strip()
    try:
        pattern = filter_text
        for key, value in metadata.items():
            if isinstance(value, str):
                if re.search(pattern, value):
                    return True
        return False
    except re.error:
        return False
        
    return True

def sanitize_folder_name(name):
    # Replace invalid characters with underscore
    invalid_chars = r'[<>:"/\\|?*]'
    sanitized = re.sub(invalid_chars, '_', name)
    # Remove leading/trailing spaces and dots
    sanitized = sanitized.strip('. ')
    # Ensure the name is not empty
    return sanitized if sanitized else 'unnamed'

def validate_search_term(search_term):
    """Validate and clean a search term, returning (cleaned_term, warnings)"""
    warnings = []
    
    if not search_term:
        return "", []
    
    # Replace multiple consecutive || with single ||
    cleaned = re.sub(r'\|{3,}', '||', search_term)
    if cleaned != search_term:
        warnings.append("Multiple consecutive OR operators (|||) were simplified to single OR (||)")
    
    # Replace multiple consecutive && with single &&
    cleaned = re.sub(r'&{3,}', '&&', cleaned)
    if cleaned != search_term:
        warnings.append("Multiple consecutive AND operators (&&&) were simplified to single AND (&&)")
    
    # Split by OR and filter out empty terms
    or_terms = [term.strip() for term in cleaned.split('||')]
    valid_or_terms = []
    
    for or_term in or_terms:
        # Split by AND and filter out empty terms
        and_terms = [term.strip() for term in or_term.split('&&')]
        valid_and_terms = [term for term in and_terms if term]
        
        if len(and_terms) != len(valid_and_terms):
            warnings.append("Empty AND terms were removed from search")
        
        if valid_and_terms:
            valid_or_terms.append(' && '.join(valid_and_terms))
    
    if len(or_terms) != len(valid_or_terms):
        warnings.append("Empty OR terms were removed from search")
    
    if not valid_or_terms:
        warnings.append("No valid search terms found after cleaning")
        return "", warnings
    
    # Reconstruct cleaned search term
    return ' || '.join(valid_or_terms), warnings

class MetadataSearcher:
    def __init__(self, search_term, recursive=False, log_path=None, copy_path=None, move_path=None, custom_filter=None, search_positive=True, search_negative=False, case_sensitive=False, ignore_term=None, lang=None):
        # Clean and validate search term and ignore term
        cleaned_term, search_warnings = validate_search_term(search_term)
        cleaned_ignore, ignore_warnings = validate_search_term(ignore_term) if ignore_term else ("", [])
        self.search_term = cleaned_term
        self.ignore_term = cleaned_ignore
        
        # Combine warnings
        warnings = search_warnings
        if ignore_warnings:
            warnings.extend(ignore_warnings)
        
        self.recursive = recursive
        self.log_path = log_path
        self.copy_path = copy_path
        self.move_path = move_path
        self.custom_filter = custom_filter
        self.match_folder_structure = True
        self.create_or_subfolders = False
        self.search_positive = search_positive
        self.search_negative = search_negative
        self.case_sensitive = case_sensitive
        self.lang = lang  # Store language manager
        self.output_text = []
        self.search_root = None
        
        # Lists to collect results
        self.found_files = []
        self.found_paths = []
        self.output_paths = []
        self.copied_files = []
        self.moved_files = []
        
        # Thread-safe logging
        self.log_lock = Lock()
        
        # Progress callback
        self.progress_callback = None
        
        # Create necessary directories
        if self.log_path:
            os.makedirs(self.log_path, exist_ok=True)
        if self.copy_path:
            os.makedirs(self.copy_path, exist_ok=True)
        if self.move_path:
            os.makedirs(self.move_path, exist_ok=True)
            
        # Log any warnings from search term validation
        for warning in warnings:
            self.log(f"Warning: {warning}")
            
        if cleaned_term != search_term:
            self.log(f"Search term was cleaned to: {cleaned_term}")

    def count_files(self, folder_path):
        """Count total number of PNG files to process"""
        total_files = 0
        if self.recursive:
            for root, _, files in os.walk(folder_path):
                total_files += sum(1 for f in files if f.lower().endswith('.png'))
        else:
            total_files = sum(1 for f in os.listdir(folder_path) if f.lower().endswith('.png'))
        return total_files

    def get_all_png_files(self, folder_path):
        """Get list of all PNG files to process"""
        png_files = []
        if self.recursive:
            for root, _, files in os.walk(folder_path):
                for file in files:
                    if file.lower().endswith('.png'):
                        png_files.append(os.path.join(root, file))
        else:
            png_files = [os.path.join(folder_path, f) for f in os.listdir(folder_path)
                        if f.lower().endswith('.png')]
        return png_files

    def process_match(self, match_data):
        if not match_data:
            return
            
        image_path, (or_index, or_term) = match_data
        filename = os.path.basename(image_path)
        self.found_files.append(filename)
        self.found_paths.append(image_path)  # Keep track of original paths
        
        # Determine the destination path based on options
        dest_path = None
        if self.copy_path or self.move_path:
            if self.create_or_subfolders:
                # Create subfolder based on the OR term
                subfolder = sanitize_folder_name(or_term)
                if self.match_folder_structure:
                    rel_path = os.path.relpath(os.path.dirname(image_path), self.search_root)
                    if rel_path == '.':
                        # File is directly in search root, don't add '.' to path
                        dest_dir = os.path.join(self.copy_path or self.move_path, subfolder)
                    else:
                        dest_dir = os.path.join(self.copy_path or self.move_path, subfolder, rel_path)
                else:
                    dest_dir = os.path.join(self.copy_path or self.move_path, subfolder)
            else:
                if self.match_folder_structure:
                    rel_path = os.path.relpath(os.path.dirname(image_path), self.search_root)
                    if rel_path == '.':
                        # File is directly in search root, don't add '.' to path
                        dest_dir = self.copy_path or self.move_path
                    else:
                        dest_dir = os.path.join(self.copy_path or self.move_path, rel_path)
                else:
                    dest_dir = self.copy_path or self.move_path
                    
            os.makedirs(dest_dir, exist_ok=True)
            dest_path = os.path.join(dest_dir, filename)
            
            if self.copy_path:
                shutil.copy2(image_path, dest_path)
                self.copied_files.append(dest_path)
            if self.move_path:
                shutil.move(image_path, dest_path)
                self.moved_files.append(dest_path)
            
            # Store the actual destination path after copy/move
            self.output_paths.append(dest_path)
        else:
            # If no copy/move, store the original path
            self.output_paths.append(image_path)

    def log(self, message):
        with self.log_lock:
            self.output_text.append(message)
            print(message)
            
            if self.log_path:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                log_file = os.path.join(self.log_path, f"log_{timestamp}.txt")
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(f"{datetime.now().isoformat()}: {message}\n")

    def set_progress_callback(self, callback):
        """Set callback function for progress updates"""
        self.progress_callback = callback

    def update_progress(self, phase, current, total):
        """Update progress through callback if set"""
        if self.progress_callback:
            self.progress_callback(phase, current, total)

    def search_images(self, folder_path):
        self.search_root = folder_path
        self.log(self.lang.get_string("messages.searching_in").format(folder_path))
        
        # Reset path lists
        self.found_files = []
        self.found_paths = []
        self.output_paths = []
        self.copied_files = []
        self.moved_files = []
        
        # Don't proceed if both search term and regex filter are invalid
        if not self.search_term and not self.custom_filter:
            self.log(self.lang.get_string("errors.no_valid_terms"))
            return
        
        # Count total files first
        self.log(self.lang.get_string("progress.counting"))
        total_files = self.count_files(folder_path)
        self.log(self.lang.get_string("progress.found_files").format(total_files))
        
        # Get list of all PNG files
        png_files = self.get_all_png_files(folder_path)
        
        # Prepare arguments for parallel processing
        process_args = [(f, self.search_term, self.search_positive, self.search_negative,
                        self.case_sensitive, self.custom_filter, self.ignore_term) for f in png_files]
        
        matching_files = 0
        processed_files = 0
        # Use ProcessPoolExecutor for parallel processing
        max_workers = max(1, multiprocessing.cpu_count() - 1)  # Leave one CPU free
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks and get futures
            futures = [executor.submit(process_single_image, arg) for arg in process_args]
            
            # Process results as they complete
            for future in tqdm(as_completed(futures), total=len(futures),
                             desc=self.lang.get_string("progress.processing"), unit="file"):
                processed_files += 1
                result = future.result()
                if result:
                    matching_files += 1
                    self.process_match(result)
                self.update_progress("search", processed_files, total_files)
        
        # Output results in organized sections
        if matching_files > 0:
            self.log("\n" + self.lang.get_string("messages.matching_files"))
            for filename in self.found_files:
                self.log(filename)
                
            # Show the correct paths - use copied_files or moved_files if we did a copy/move
            if self.copy_path or self.move_path:
                self.log("\n" + self.lang.get_string("messages.complete_paths"))
                if self.copy_path:
                    for path in self.copied_files:
                        self.log(path)
                if self.move_path:
                    for path in self.moved_files:
                        self.log(path)
            else:
                # No copy or move, show original paths
                self.log("\n" + self.lang.get_string("messages.complete_paths"))
                for path in self.found_paths:
                    self.log(path)
        
        # Output summary
        self.log("\n" + self.lang.get_string("messages.summary"))
        self.log(self.lang.get_string("messages.total_files").format(total_files))
        self.log(self.lang.get_string("messages.matches_found").format(matching_files))
        
        actions = []
        if self.log_path:
            actions.append(self.lang.get_string("messages.logged_files"))
        if self.copy_path:
            actions.append(self.lang.get_string("messages.copied_files").format(len(self.copied_files)))
        if self.move_path:
            actions.append(self.lang.get_string("messages.moved_files").format(len(self.moved_files)))
            
        if actions:
            self.log(self.lang.get_string("messages.actions_taken").format(", ".join(actions)))

class SearchGUI:
    def __init__(self):
        self.root = tk.Tk()
        
        # Initialize config manager
        self.config = ConfigManagerMetadataSearch()
        
        # Initialize language manager with config
        initial_language = self.config.get("Interface", "language", "English")
        self.lang = LanguageManagerMetadataSearch("metadatasearch", initial_language)
        self._initial_language = initial_language  # Store initial language
        
        self.root.title(self.lang.get_string("window.title"))
        self.root.geometry("900x600")
        
        # Create menu bar
        self.menubar = tk.Menu(self.root)
        self.root.config(menu=self.menubar)
        
        # Create Language menu
        self.language_menu = tk.Menu(self.menubar, tearoff=0)
        menu_label = self.lang.get_string("menu.language")
        self.menubar.add_cascade(label=menu_label, menu=self.language_menu)
        
        # Add language options
        language_names = {lang: self._get_language_name(lang) for lang in self.lang.get_languages()}
        
        # Add English first
        if 'English' in language_names:
            self.language_menu.add_command(
                label=language_names['English'],
                command=lambda l='English': self._on_language_change(l)
            )
            self.language_menu.add_separator()
            del language_names['English']
        
        # Add other languages in alphabetical order
        for lang_code, lang_name in sorted(language_names.items(), key=lambda x: x[1]):
            self.language_menu.add_command(
                label=lang_name,
                command=lambda l=lang_code: self._on_language_change(l)
            )
        
        # Store browse button references
        self.browse_buttons = []
        
        # Create and configure main frame
        self.main_frame = ttk.Frame(self.root, padding="10")
        self.main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        
        # Configure style for centered combobox
        style = ttk.Style()
        style.configure('Centered.TCombobox', justify='center')
        
        # Folder selection
        folder_label = ttk.Label(self.main_frame, text=self.lang.get_string("labels.folder_path"), width=20, anchor='e')
        folder_label.grid(row=0, column=0, sticky=tk.W)
        self._add_tooltip(folder_label, "folder_path")
        
        self.folder_path = tk.StringVar(value=self.config.get("Paths", "default_search_folder", ""))
        self.folder_entry = ttk.Entry(self.main_frame, textvariable=self.folder_path)
        self.folder_entry.grid(row=0, column=1, columnspan=2, sticky=(tk.W, tk.E))
        
        self.browse_button = ttk.Button(self.main_frame, text=self.lang.get_string("buttons.browse"), 
                                      command=self.browse_folder, width=20)
        self.browse_button.grid(row=0, column=3, padx=5)
        self.browse_buttons.append(self.browse_button)
        
        # Search term and ignore term
        search_label = ttk.Label(self.main_frame, text=self.lang.get_string("labels.search_term"), width=20, anchor='e')
        search_label.grid(row=1, column=0, sticky=tk.W)
        self._add_tooltip(search_label, "search_term")
        
        self.search_term = tk.StringVar(value=self.config.get("Search", "search_term", ""))
        search_entry = ttk.Entry(self.main_frame, textvariable=self.search_term)
        search_entry.grid(row=1, column=1, columnspan=2, sticky=(tk.W, tk.E))
        
        # Ignore term
        ignore_label = ttk.Label(self.main_frame, text=self.lang.get_string("labels.ignore_term"), width=20, anchor='e')
        ignore_label.grid(row=2, column=0, sticky=tk.W)
        self._add_tooltip(ignore_label, "ignore_term")
        
        self.ignore_term = tk.StringVar(value=self.config.get("Search", "ignore_term", ""))
        ignore_entry = ttk.Entry(self.main_frame, textvariable=self.ignore_term)
        ignore_entry.grid(row=2, column=1, columnspan=2, sticky=(tk.W, tk.E))
        
        # Options frame
        options_frame = ttk.LabelFrame(self.main_frame, text=self.lang.get_string("frames.options"), padding="5")
        options_frame.grid(row=3, column=0, columnspan=4, sticky=(tk.W, tk.E), pady=10)
        options_frame.columnconfigure(1, weight=1)
        options_frame.frame_id = "options"  # Add frame identifier
        
        # Checkboxes frame
        checkbox_frame = ttk.Frame(options_frame)
        checkbox_frame.grid(row=0, column=0, columnspan=4, sticky=(tk.W, tk.E))
        
        # First row of checkboxes
        self.recursive = tk.BooleanVar(value=self.config.get_bool("Search", "recursive", True))
        recursive_cb = ttk.Checkbutton(checkbox_frame, text=self.lang.get_string("checkboxes.recursive.text"),
                                     variable=self.recursive)
        recursive_cb.grid(row=0, column=0, sticky=tk.W, padx=5)
        self._add_tooltip(recursive_cb, "recursive")
        
        self.log_enabled = tk.BooleanVar(value=self.config.get_bool("Output", "enable_logging", False))
        log_cb = ttk.Checkbutton(checkbox_frame, text=self.lang.get_string("checkboxes.logging.text"),
                                variable=self.log_enabled)
        log_cb.grid(row=0, column=1, sticky=tk.W, padx=5)
        self._add_tooltip(log_cb, "logging")
        
        self.match_folder_structure = tk.BooleanVar(value=self.config.get_bool("Output", "match_folder_structure", True))
        match_cb = ttk.Checkbutton(checkbox_frame, text=self.lang.get_string("checkboxes.match_structure.text"),
                                  variable=self.match_folder_structure)
        match_cb.grid(row=0, column=2, sticky=tk.W, padx=5)
        self._add_tooltip(match_cb, "match_structure")
        
        self.create_or_subfolders = tk.BooleanVar(value=self.config.get_bool("Output", "create_or_subfolders", False))
        or_cb = ttk.Checkbutton(checkbox_frame, text=self.lang.get_string("checkboxes.or_subfolders.text"),
                               variable=self.create_or_subfolders)
        or_cb.grid(row=0, column=3, sticky=tk.W, padx=5)
        self._add_tooltip(or_cb, "or_subfolders")
        
        # Second row of checkboxes
        self.search_positive = tk.BooleanVar(value=self.config.get_bool("Search", "search_positive", True))
        pos_cb = ttk.Checkbutton(checkbox_frame, text=self.lang.get_string("checkboxes.search_positive.text"),
                                variable=self.search_positive)
        pos_cb.grid(row=1, column=0, sticky=tk.W, padx=5)
        self._add_tooltip(pos_cb, "search_positive")
        
        self.search_negative = tk.BooleanVar(value=self.config.get_bool("Search", "search_negative", False))
        neg_cb = ttk.Checkbutton(checkbox_frame, text=self.lang.get_string("checkboxes.search_negative.text"),
                                variable=self.search_negative)
        neg_cb.grid(row=1, column=1, sticky=tk.W, padx=5)
        self._add_tooltip(neg_cb, "search_negative")
        
        self.case_sensitive = tk.BooleanVar(value=self.config.get_bool("Search", "case_sensitive", False))
        case_cb = ttk.Checkbutton(checkbox_frame, text=self.lang.get_string("checkboxes.case_sensitive.text"),
                                 variable=self.case_sensitive)
        case_cb.grid(row=1, column=2, sticky=tk.W, padx=5)
        self._add_tooltip(case_cb, "case_sensitive")
        
        # Copy/Move options
        copy_label = ttk.Label(options_frame, text=self.lang.get_string("labels.copy_to"), width=20, anchor='e')
        copy_label.grid(row=2, column=0, sticky=tk.W)
        self._add_tooltip(copy_label, "copy_to")
        
        self.copy_path = tk.StringVar(value=self.config.get("Paths", "default_copy_folder", ""))
        copy_entry = ttk.Entry(options_frame, textvariable=self.copy_path)
        copy_entry.grid(row=2, column=1, columnspan=2, sticky=(tk.W, tk.E))
        
        copy_browse = ttk.Button(options_frame, text=self.lang.get_string("buttons.browse"),
                  command=lambda: self.browse_output("copy"), width=20)
        copy_browse.grid(row=2, column=3, padx=5)
        self.browse_buttons.append(copy_browse)
        
        move_label = ttk.Label(options_frame, text=self.lang.get_string("labels.move_to"), width=20, anchor='e')
        move_label.grid(row=3, column=0, sticky=tk.W)
        self._add_tooltip(move_label, "move_to")
        
        self.move_path = tk.StringVar(value=self.config.get("Paths", "default_move_folder", ""))
        move_entry = ttk.Entry(options_frame, textvariable=self.move_path)
        move_entry.grid(row=3, column=1, columnspan=2, sticky=(tk.W, tk.E))
        
        move_browse = ttk.Button(options_frame, text=self.lang.get_string("buttons.browse"),
                  command=lambda: self.browse_output("move"), width=20)
        move_browse.grid(row=3, column=3, padx=5)
        self.browse_buttons.append(move_browse)
        
        # Custom filter (one line)
        custom_label = ttk.Label(options_frame, text=self.lang.get_string("labels.regex"), width=20, anchor='e')
        custom_label.grid(row=4, column=0, sticky=tk.W)
        self._add_tooltip(custom_label, "regex_filter")
        
        self.custom_filter = ttk.Entry(options_frame)
        self.custom_filter.grid(row=4, column=1, columnspan=2, sticky=(tk.W, tk.E))
        
        # Search button
        self.search_button = ttk.Button(self.main_frame, text=self.lang.get_string("buttons.search"),
                                      command=self.start_search, width=30)
        self.search_button.grid(row=4, column=0, columnspan=4, pady=10, padx=10)
        # Add internal padding to make button taller
        self.search_button.configure(padding=(10, 5))
        
        # Progress frame
        progress_frame = ttk.LabelFrame(self.main_frame, text=self.lang.get_string("frames.progress"), padding="5")
        progress_frame.grid(row=5, column=0, columnspan=4, sticky=(tk.W, tk.E), pady=5)
        progress_frame.columnconfigure(0, weight=1)
        progress_frame.frame_id = "progress"  # Add frame identifier
        
        # Progress bars
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=5, pady=5)
        
        self.progress_label = ttk.Label(progress_frame, text=self.lang.get_string("progress.ready"))
        self.progress_label.grid(row=1, column=0, sticky=tk.W, padx=5)
        
        # Output text area (without frame)
        self.output_area = scrolledtext.ScrolledText(self.main_frame, height=15)
        self.output_area.grid(row=6, column=0, columnspan=4, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure grid weights
        self.main_frame.columnconfigure(1, weight=1)
        self.main_frame.rowconfigure(6, weight=1)
        
        # Bind closing event
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _add_tooltip(self, widget, key):
        """Add a tooltip to a widget"""
        widget.tooltip_key = key  # Store the key for later updates
        tooltip_text = self.lang.get_tooltip(key)
        if tooltip_text:
            widget.tooltip = tooltip_text
            
            def show_tooltip(event):
                if hasattr(widget, 'tooltip_window'):
                    widget.tooltip_window.destroy()
                tooltip = tk.Toplevel()
                tooltip.wm_overrideredirect(True)
                tooltip.wm_geometry(f"+{event.x_root+10}+{event.y_root+10}")
                
                label = ttk.Label(tooltip, text=widget.tooltip, justify='left',
                                relief='solid', borderwidth=1)
                label.pack()
                
                widget.tooltip_window = tooltip
            
            def hide_tooltip(event):
                if hasattr(widget, 'tooltip_window'):
                    widget.tooltip_window.destroy()
                    del widget.tooltip_window
            
            widget.bind('<Enter>', show_tooltip)
            widget.bind('<Leave>', hide_tooltip)

    def _on_language_change(self, lang_code):
        """Handle language change event"""
        # Update language manager
        self.lang.set_language(lang_code)
        
        # Update config
        self.config.set("Interface", "language", lang_code)
        self.config.save_config()
        
        # Update all GUI strings
        self._update_gui_strings()
        
        # Update progress label
        self.progress_label.config(text=self.lang.get_string("progress.ready"))
        
        # Update menu labels
        for i in range(self.menubar.index("end") + 1):
            if self.menubar.type(i) == "cascade":
                menu_widget = self.menubar.nametowidget(self.menubar.entrycget(i, "menu"))
                if menu_widget == self.language_menu:
                    new_label = self.lang.get_string("menu.language")
                    self.menubar.entryconfigure(i, label=new_label)
                    break

    def _update_gui_strings(self):
        """Update all GUI strings after language change"""
        # Update window title
        self.root.title(self.lang.get_string("window.title"))
        
        # Helper function to update widgets in a container
        def update_container_widgets(container):
            for widget in container.winfo_children():
                if isinstance(widget, ttk.Label):
                    grid_info = widget.grid_info()
                    if isinstance(container, ttk.LabelFrame) and container.winfo_children()[0] == widget:
                        # Skip labels that are part of a LabelFrame (they're handled separately)
                        continue
                    
                    # Handle main frame labels
                    if container == self.main_frame:
                        if grid_info['row'] == 0 and grid_info['column'] == 0:
                            widget.config(text=self.lang.get_string("labels.folder_path"))
                        elif grid_info['row'] == 1 and grid_info['column'] == 0:
                            widget.config(text=self.lang.get_string("labels.search_term"))
                        elif grid_info['row'] == 2 and grid_info['column'] == 0:
                            widget.config(text=self.lang.get_string("labels.ignore_term"))
                    # Handle options frame labels
                    elif isinstance(container, ttk.LabelFrame) and hasattr(container, 'frame_id') and container.frame_id == "options":
                        if grid_info['row'] == 2 and grid_info['column'] == 0:
                            widget.config(text=self.lang.get_string("labels.copy_to"))
                        elif grid_info['row'] == 3 and grid_info['column'] == 0:
                            widget.config(text=self.lang.get_string("labels.move_to"))
                        elif grid_info['row'] == 4 and grid_info['column'] == 0:
                            widget.config(text=self.lang.get_string("labels.regex"))
                elif isinstance(widget, ttk.Button):
                    if widget == self.search_button:
                        widget.config(text=self.lang.get_string("buttons.search"))
                    elif widget in self.browse_buttons:
                        widget.config(text=self.lang.get_string("buttons.browse"))
                elif isinstance(widget, ttk.Checkbutton):
                    # Update checkbox text based on its grid position in its parent
                    grid_info = widget.grid_info()
                    if grid_info['row'] == 0:
                        if grid_info['column'] == 0:
                            widget.config(text=self.lang.get_string("checkboxes.recursive.text"))
                        elif grid_info['column'] == 1:
                            widget.config(text=self.lang.get_string("checkboxes.logging.text"))
                        elif grid_info['column'] == 2:
                            widget.config(text=self.lang.get_string("checkboxes.match_structure.text"))
                        elif grid_info['column'] == 3:
                            widget.config(text=self.lang.get_string("checkboxes.or_subfolders.text"))
                    elif grid_info['row'] == 1:
                        if grid_info['column'] == 0:
                            widget.config(text=self.lang.get_string("checkboxes.search_positive.text"))
                        elif grid_info['column'] == 1:
                            widget.config(text=self.lang.get_string("checkboxes.search_negative.text"))
                        elif grid_info['column'] == 2:
                            widget.config(text=self.lang.get_string("checkboxes.case_sensitive.text"))
                elif isinstance(widget, ttk.LabelFrame):
                    # Update frame titles using frame_id
                    if hasattr(widget, 'frame_id'):
                        widget.config(text=self.lang.get_string(f"frames.{widget.frame_id}"))
                    # Recursively update widgets in the LabelFrame
                    update_container_widgets(widget)
                elif isinstance(widget, ttk.Frame):
                    # Recursively update widgets in the Frame
                    update_container_widgets(widget)
        
        # Update all widgets starting from main frame
        update_container_widgets(self.main_frame)
        
        # Update progress label
        self.progress_label.config(text=self.lang.get_string("progress.ready"))
        
        # Update tooltips
        self._update_tooltips()

    def _update_tooltips(self):
        """Update tooltips for all widgets"""
        def update_container_tooltips(container):
            for widget in container.winfo_children():
                if hasattr(widget, 'tooltip_key'):
                    tooltip_text = self.lang.get_tooltip(widget.tooltip_key)
                    if tooltip_text:
                        widget.tooltip = tooltip_text
                
                # Recursively update tooltips in child containers
                if isinstance(widget, (ttk.Frame, ttk.LabelFrame)):
                    update_container_tooltips(widget)
        
        # Update all tooltips starting from main frame
        update_container_tooltips(self.main_frame)

    def _get_language_name(self, lang_code: str) -> str:
        """Get the display name for a language code"""
        self.lang.set_language(lang_code)
        name = self.lang.get_string("language.name")
        self.lang.set_language(self._initial_language)  # Restore using stored initial language
        return name

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder_path.set(folder)

    def browse_output(self, output_type):
        folder = filedialog.askdirectory()
        if folder:
            if output_type == "copy":
                self.copy_path.set(folder)
            else:
                self.move_path.set(folder)

    def log_output(self, message):
        self.output_area.insert(tk.END, message + "\n")
        self.output_area.see(tk.END)
        self.root.update_idletasks()

    def update_progress(self, phase, current, total):
        if total > 0:
            progress = (current / total) * 100
            self.progress_var.set(progress)
            phase_text = self.lang.get_string(f"progress.{phase}")
            self.progress_label.config(text=f"{phase_text}: {current}/{total} ({progress:.1f}%)")
        self.root.update_idletasks()

    def _confirm_action(self, action_type):
        """Show confirmation dialog for dangerous actions"""
        if action_type == "move":
            return messagebox.askyesno(
                self.lang.get_string("confirmations.move_title"),
                self.lang.get_string("confirmations.move_message")
            )
        return True

    def start_search(self):
        # Clear output area and reset progress
        self.output_area.delete(1.0, tk.END)
        self.progress_var.set(0)
        self.progress_label.config(text=self.lang.get_string("progress.starting"))
        self.search_button.state(['disabled'])
        
        try:
            # Check for move operation and get confirmation
            if self.move_path.get():
                if not self._confirm_action("move"):
                    self.search_button.state(['!disabled'])
                    return
            
            # Create searcher instance
            searcher = MetadataSearcher(
                search_term=self.search_term.get(),
                recursive=self.recursive.get(),
                log_path="logs" if self.log_enabled.get() else None,
                copy_path=self.copy_path.get() or None,
                move_path=self.move_path.get() or None,
                custom_filter=self.custom_filter.get() or None,
                search_positive=self.search_positive.get(),
                search_negative=self.search_negative.get(),
                case_sensitive=self.case_sensitive.get(),
                ignore_term=self.ignore_term.get() or None,
                lang=self.lang  # Pass language manager to searcher
            )
            
            # Set match folder structure option
            searcher.match_folder_structure = self.match_folder_structure.get()
            
            # Set create OR subfolders option
            searcher.create_or_subfolders = self.create_or_subfolders.get()
            
            # Set progress callback
            searcher.set_progress_callback(self.update_progress)
            
            # Redirect searcher output to GUI
            original_log = searcher.log
            searcher.log = lambda msg: [original_log(msg), self.log_output(msg)]
            
            # Start search
            searcher.search_images(self.folder_path.get())
            self.log_output("\n" + self.lang.get_string("progress.completed"))
            
        except Exception as e:
            self.log_output("\n" + self.lang.get_string("errors.search_error").format(str(e)))
        finally:
            self.search_button.state(['!disabled'])
            self.progress_label.config(text=self.lang.get_string("progress.ready"))

    def _on_closing(self):
        """Save settings before closing"""
        self.config.set("Interface", "language", self.lang.current_language)
        self.config.set("Search", "recursive", str(self.recursive.get()))
        self.config.set("Search", "case_sensitive", str(self.case_sensitive.get()))
        self.config.set("Search", "search_positive", str(self.search_positive.get()))
        self.config.set("Search", "search_negative", str(self.search_negative.get()))
        self.config.set("Search", "search_term", self.search_term.get())
        self.config.set("Search", "ignore_term", self.ignore_term.get())
        self.config.set("Output", "match_folder_structure", str(self.match_folder_structure.get()))
        self.config.set("Output", "create_or_subfolders", str(self.create_or_subfolders.get()))
        self.config.set("Output", "enable_logging", str(self.log_enabled.get()))
        self.config.set("Paths", "default_search_folder", self.folder_path.get())
        self.config.set("Paths", "default_copy_folder", self.copy_path.get())
        self.config.set("Paths", "default_move_folder", self.move_path.get())
        self.config.save_config()
        self.root.destroy()

def parse_args():
    parser = argparse.ArgumentParser(description="Search PNG images for metadata matching a search term")
    parser.add_argument("--folder", help="Folder to search in")
    parser.add_argument("--term", help="Search term (supports wildcards)")
    parser.add_argument("--recursive", action="store_true", help="Search recursively")
    parser.add_argument("--log-path", help="Path to store log files")
    parser.add_argument("--copy-to", help="Copy matching files to this folder")
    parser.add_argument("--move-to", help="Move matching files to this folder")
    parser.add_argument("--filter", help="Path to custom filter script (not implemented)")
    parser.add_argument("--case-sensitive", action="store_true", help="Enable case sensitive search")
    return parser.parse_args()

def main():
    args = parse_args()
    
    # If command line arguments are provided, run in CLI mode
    if args.folder and args.term:
        searcher = MetadataSearcher(
            search_term=args.term,
            recursive=args.recursive,
            log_path=args.log_path,
            copy_path=args.copy_to,
            move_path=args.move_to,
            custom_filter=args.filter,
            case_sensitive=args.case_sensitive
        )
        searcher.search_images(args.folder)
    # Otherwise, launch GUI
    else:
        gui = SearchGUI()
        gui.root.mainloop()

if __name__ == "__main__":
    main() 