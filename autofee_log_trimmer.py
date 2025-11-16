#!/usr/bin/env python3
"""
Simple log trimmer - keeps last N lines or trims to max size
No complex parsing, no archives, just smaller logs
"""

import os
import shutil

# Configuration
LOG_DIR = os.path.expanduser('~/autofee')
MAX_LINES = 50000  # Keep last 50k lines per file (~5-10MB depending on content)
MAX_SIZE_MB = 10   # Alternative: trim if larger than 10MB

# Log files to trim
LOG_FILES = [
    'autofee_wrapper.log',
    'autofee_neginb_wrapper.log', 
    'autofee_stagnant_wrapper.log',
    'autofee_maxhtlc_wrapper.log',
    'cron.log',
    'autofee_report.log'
]

def trim_log_file(log_file, max_lines=MAX_LINES):
    """Trim log file to last N lines if over size limit"""
    log_path = os.path.join(LOG_DIR, log_file)
    
    if not os.path.exists(log_path):
        return 0, 0
        
    try:
        # Get original size
        original_size = os.path.getsize(log_path)
        
        # Skip if file is under size limit
        if original_size <= MAX_SIZE_MB * 1024 * 1024:
            return 0, 0
            
        # Read file
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            
        # Skip if already under line limit
        if len(lines) <= max_lines:
            return 0, 0
            
        # Keep last N lines
        kept_lines = lines[-max_lines:]
        
        # Write back
        temp_file = log_path + '.tmp'
        with open(temp_file, 'w', encoding='utf-8') as f:
            f.writelines(kept_lines)
            
        # Replace original
        shutil.move(temp_file, log_path)
        
        new_size = os.path.getsize(log_path)
        lines_removed = len(lines) - len(kept_lines)
        bytes_freed = original_size - new_size
        
        return lines_removed, bytes_freed
        
    except Exception as e:
        print(f"Error trimming {log_file}: {e}")
        # Clean up temp file if exists
        temp_file = log_path + '.tmp'
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass
        return 0, 0

def format_bytes(bytes_value):
    """Format bytes to human readable string"""
    if bytes_value <= 0:
        return "0 B"
    
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_value < 1024.0:
            return f"{int(bytes_value)} {unit}" if unit == 'B' else f"{bytes_value:.1f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.1f} TB"

def main():
    print(f"=== Simple Log Trimmer ===")
    print(f"Keeping last {MAX_LINES:,} lines per file")
    print(f"Only trimming files larger than {MAX_SIZE_MB}MB\n")
    
    total_lines_removed = 0
    total_bytes_freed = 0
    
    for log_file in LOG_FILES:
        # Show current size
        log_path = os.path.join(LOG_DIR, log_file)
        if os.path.exists(log_path):
            current_size = os.path.getsize(log_path)
            print(f"{log_file}: {format_bytes(current_size)}", end="")
            
            lines_removed, bytes_freed = trim_log_file(log_file)
            
            if lines_removed > 0:
                print(f" -> trimmed {lines_removed:,} lines, freed {format_bytes(bytes_freed)}")
                total_lines_removed += lines_removed
                total_bytes_freed += bytes_freed
            else:
                print(" (no trim needed)")
    
    if total_bytes_freed > 0:
        print(f"\nTotal: Removed {total_lines_removed:,} lines, freed {format_bytes(total_bytes_freed)}")
    else:
        print("\nNo trimming needed - all files within size limits")

if __name__ == "__main__":
    main()