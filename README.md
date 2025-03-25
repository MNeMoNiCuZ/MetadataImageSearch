# Metadata Image Search Tool

A tool for searching PNG images based on their metadata, particularly useful for AI-generated images. Search through positive and negative prompts, and organize matching files with various output options.

## Requirements

- Python 3.6 or higher
- Windows operating system (for the provided batch scripts)

## Installation

1. Clone or download this repository
2. Run `venv_create.bat` to create a virtual environment:
   - Choose your Python version when prompted
   - Accept the default virtual environment name (venv) or choose your own
   - Allow pip upgrade when prompted
   - Allow installation of dependencies from requirements.txt

The script will create:
- A virtual environment
- `venv_activate.bat` for activating the environment
- `venv_update.bat` for updating pip

## Usage

### GUI Mode (Default)
Run `python metadata_search.py` to launch the graphical interface.

#### Search Options
- **Folder Path**: Select the folder containing PNG images to search
- **Search Term**: Enter your search criteria
  - Use `&&` for AND operations (all terms must match)
  - Use `||` for OR operations (any term can match)
  - Example: `cat && black || dog && brown`
- **Ignore Term**: Terms that will exclude matching files
  - Uses same syntax as Search Term
- **Recursive Search**: Search in subfolders
- **Case Sensitive**: Enable exact case matching
- **Search Positive/Negative**: Choose which prompts to search in

#### Output Options
- **Copy To**: Copy matching files to specified folder
- **Move To**: Move matching files to specified folder
- **Match Folder Structure**: Preserve original folder structure in output
- **Create OR Subfolders**: Create separate folders for each OR term match
- **Enable Logging**: Save search results to log files

### Command Line Interface

Run `python metadata_search.py --help` for all available options.

Basic usage:
```bash
python metadata_search.py --folder "path/to/images" --term "search term"
```

Options:
- `--folder`: Folder to search in
- `--term`: Search term
- `--recursive`: Search in subfolders
- `--log-path`: Path for log files
- `--copy-to`: Copy matching files
- `--move-to`: Move matching files
- `--case-sensitive`: Enable case sensitive search

## Features

- Search through AI-generated image metadata
- Complex search patterns with AND/OR operators
- Filter by positive and negative prompts
- Custom regex filtering
- Preserve folder structure in output
- Create organized subfolders based on search matches
- Comprehensive logging system
- Both GUI and CLI interfaces

## Notes

- The tool is designed primarily for PNG images with metadata
- Search terms support wildcards (* and ?)
- Logs are saved with timestamps in the logs folder
- Moving files is permanent - use with caution 