import os
import re

from config_store import load_setting


RE_O_NUMBER = re.compile(r"\bO\s*0*(\d+)\b", re.IGNORECASE)
RE_O_WITH_NAME = re.compile(r"\bO\s*0*(\d+)\s*\(([^)]+)\)", re.IGNORECASE)
RE_PAREN_NAME = re.compile(r"\(([^)\n]{2,60})\)")
RE_COMMENT_NAME = re.compile(
    r"\bPROG(?:RAM)?\s*[:=]?\s*([A-Z0-9_\- ]{2,60})", re.IGNORECASE
)
RE_T_NUMBER = re.compile(r'T\s*0*(\d+)', re.IGNORECASE)
RE_TOOL_CHANGE = re.compile(r'M\s*0*6(?!\d)', re.IGNORECASE)
RE_PAREN_COMMENT = re.compile(r'\([^)]*\)')

INTERNAL_FILES = {'index.json', 'config.json', 'alati.json'}


def find_cnc_files(folder):
    ignored_extensions = set(load_setting('ignored_extensions', []))
    for root, _dirs, files in os.walk(folder):
        for filename in files:
            if filename.lower() in INTERNAL_FILES:
                continue
            extension = os.path.splitext(filename)[1].lower()
            if extension in ignored_extensions:
                continue
            yield os.path.join(root, filename)


def extract_tools_from_program(content):
    tools = set()
    pending_tool = None
    for raw_line in content.splitlines():
        line = RE_PAREN_COMMENT.sub('', raw_line.upper()).lstrip('/')
        if not line.strip():
            continue
        match = RE_T_NUMBER.search(line)
        if match:
            try:
                pending_tool = int(match.group(1))
            except (TypeError, ValueError):
                pending_tool = None
        if RE_TOOL_CHANGE.search(line) and pending_tool is not None:
            tools.add(pending_tool)
    return sorted(tools)


def parse_fanuc_header(path):
    program_number = None
    program_name = None
    try:
        with open(path, 'r', errors='ignore') as program_file:
            content = program_file.read(4096)
        match = RE_O_WITH_NAME.search(content)
        if match:
            program_number = match.group(1)
            program_name = match.group(2).strip()
        else:
            number_match = RE_O_NUMBER.search(content)
            if number_match:
                program_number = number_match.group(1)
            name_match = RE_PAREN_NAME.search(content)
            if name_match and len(name_match.group(1).strip()) > 1:
                program_name = name_match.group(1).strip()
            comment_match = RE_COMMENT_NAME.search(content)
            if comment_match:
                program_name = comment_match.group(1).strip()
    except OSError:
        pass
    return {'program_number': program_number, 'program_name': program_name}


def load_file_content(path):
    try:
        with open(path, 'r', errors='ignore') as program_file:
            return program_file.read()
    except OSError as error:
        return f'Ne mogu učitati datoteku: {path} ({error})'


def contains_weird_chars_bytes(path, chunk_size=8192):
    try:
        with open(path, 'rb') as program_file:
            for chunk in iter(lambda: program_file.read(chunk_size), b''):
                if any(byte > 127 for byte in chunk):
                    return True
        return False
    except OSError:
        return True


def contains_weird_chars_content(content):
    return any(ord(character) > 127 for character in content)
