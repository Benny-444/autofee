#!/usr/bin/env python3
"""
Show all configuration settings/constants from autofee Python scripts
"""

import os
import re
import sys

# Directory containing the autofee scripts
AUTOFEE_DIR = os.path.expanduser('~/autofee')

# Python files to analyze
PYTHON_FILES = [
    'autofee_wrapper.py',
    'autofee_neginb_wrapper.py',
    'autofee_stagnant_wrapper.py',
    'autofee_maxhtlc_wrapper.py',
    'autofee_pivot_wrapper.py',
#    'autofee_report.py',
    'autofee_log_trimmer.py'
]

def extract_constants(file_path):
    """Extract constants/settings from a Python file"""
    constants = []

    # Constants to skip (reduce noise)
    skip_constants = {
        'LOG_FILES', 'COLORS', 'CONFIG',
        'EXCLUDE_CHAN_IDS', 'CHAN_IDS'
    }

    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()

        for line_num, line in enumerate(lines, 1):
            line = line.strip()

            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue

            # Look for constants (uppercase variables assigned values)
            # Pattern: CONSTANT_NAME = value  (with optional comment)
            match = re.match(r'^([A-Z][A-Z0-9_]*)\s*=\s*(.+?)(?:\s*#.*)?$', line)
            if match:
                const_name = match.group(1)
                const_value = match.group(2).strip()

                # Skip noisy constants
                if const_name in skip_constants:
                    continue

                # Skip constants ending with _FILE
                if const_name.endswith('_FILE'):
                    continue
                # Skip constants ending with _DIR
                if const_name.endswith('_DIR'):
                    continue
                # Skip constants ending with _CHECK
                if const_name.endswith('_CHECK'):
                    continue
                # Extract inline comment if present
                comment_match = re.search(r'#\s*(.+)$', line)
                comment = comment_match.group(1) if comment_match else ""

                constants.append({
                    'name': const_name,
                    'value': const_value,
                    'comment': comment,
                    'line': line_num
                })

    except Exception as e:
        print(f"Error reading {file_path}: {e}")

    return constants

def format_value(value):
    """Format value for display"""
    # Remove quotes from strings for cleaner display
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value

def main():
    """Main function to display all settings"""
    print("=" * 80)
    print("AUTOFEE CONFIGURATION SETTINGS")
    print("=" * 80)

    for script_name in PYTHON_FILES:
        file_path = os.path.join(AUTOFEE_DIR, script_name)

        if not os.path.exists(file_path):
            print(f"\n❌ {script_name} - FILE NOT FOUND")
            continue

        constants = extract_constants(file_path)

        if not constants:
            print(f"\n📄 {script_name} - No configuration constants found")
            continue

        print(f"\n📄 {script_name.upper()}")
        print("-" * 60)

        # Find the longest constant name for alignment
        max_name_len = max(len(const['name']) for const in constants)
        max_value_len = max(len(format_value(const['value'])) for const in constants)

        for const in constants:
            name = const['name']
            value = format_value(const['value'])
            comment = const['comment']

            # Format the line
            line = f"{name:<{max_name_len}} = {value:<{max_value_len}}"

            if comment:
                line += f"  # {comment}"

            print(f"  {line}")

    print("\n" + "=" * 80)
    print("Settings shown above are read directly from the Python files.")
    print("Modify the files directly to change these values.")
    print("=" * 80)

if __name__ == "__main__":
    main()