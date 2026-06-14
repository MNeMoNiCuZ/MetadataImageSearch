import os
import json
from typing import Dict, Any, List

def load_json_file(file_path: str) -> Dict[str, Any]:
    """Load a JSON file with UTF-8 encoding"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_all_keys(d: Dict[str, Any], prefix: str = '') -> List[str]:
    """Get all keys from a nested dictionary with dot notation"""
    keys = []
    for k, v in d.items():
        new_key = f"{prefix}.{k}" if prefix else k
        keys.append(new_key)
        if isinstance(v, dict):
            keys.extend(get_all_keys(v, new_key))
    return keys

def find_missing_keys(reference: Dict[str, Any], target: Dict[str, Any]) -> List[str]:
    """Find keys that exist in reference but not in target"""
    ref_keys = set(get_all_keys(reference))
    target_keys = set(get_all_keys(target))
    return sorted(list(ref_keys - target_keys))

def main():
    # Get the directory of this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Load English files as reference
    everything_en = load_json_file(os.path.join(script_dir, 'everything-en.json'))
    metadatasearch_en = load_json_file(os.path.join(script_dir, 'metadatasearch-en.json'))
    
    # Get all language files
    lang_files = [f for f in os.listdir(script_dir) if f.endswith('.json') and f != 'everything-en.json' and f != 'metadatasearch-en.json']
    
    print("\nLanguage Comparison Report")
    print("=" * 50)
    
    for lang_file in lang_files:
        print(f"\nChecking {lang_file}:")
        print("-" * 30)
        
        try:
            lang_data = load_json_file(os.path.join(script_dir, lang_file))
            
            # Determine which English file to use as reference
            if lang_file.startswith('everything-'):
                ref_data = everything_en
                ref_name = 'everything-en.json'
            else:
                ref_data = metadatasearch_en
                ref_name = 'metadatasearch-en.json'
            
            # Find missing keys
            missing_keys = find_missing_keys(ref_data, lang_data)
            
            if missing_keys:
                print(f"Missing keys (compared to {ref_name}):")
                for key in missing_keys:
                    print(f"  - {key}")
            else:
                print("No missing keys!")
                
        except Exception as e:
            print(f"Error processing {lang_file}: {str(e)}")

if __name__ == "__main__":
    main() 