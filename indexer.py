import datetime
import hashlib
import json
import os

from config_store import write_json_atomic
from fanuc_utils import (
    contains_weird_chars_bytes,
    find_cnc_files,
    parse_fanuc_header,
)


class Indexer:
    def __init__(self):
        self.entries = []
        self.index_path = None

    @staticmethod
    def compute_md5(path, block_size=65536):
        try:
            digest = hashlib.md5()
            with open(path, 'rb') as source_file:
                for chunk in iter(lambda: source_file.read(block_size), b''):
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            return None

    @staticmethod
    def _new_entry(path, modified):
        info = parse_fanuc_header(path)
        return {
            'filepath': os.path.abspath(path),
            'filename': os.path.basename(path),
            'program_number': info['program_number'],
            'program_name': info['program_name'],
            'modified': modified,
            'problem': contains_weird_chars_bytes(path),
            'md5': Indexer.compute_md5(path),
            'duplicate': False,
        }

    def scan_folder(self, folder, update_progress=None):
        files = list(find_cnc_files(folder))
        self.entries = []
        for position, path in enumerate(files, 1):
            try:
                modified = os.stat(path).st_mtime
            except OSError:
                modified = 0
            self.entries.append(self._new_entry(path, modified))
            if update_progress:
                update_progress(position, len(files))

        self._mark_duplicates()
        self.save_index(os.path.join(folder, 'index.json'))
        return len(files)

    def load_index(self, index_file):
        with open(index_file, 'r', encoding='utf-8') as json_file:
            data = json.load(json_file)
        self.entries = data.get('entries', [])
        self.index_path = index_file
        for entry in self.entries:
            entry.setdefault('md5', None)
            entry.setdefault('duplicate', False)

    def save_index(self, path=None):
        path = path or self.index_path
        if not path:
            raise ValueError('Nije definirana putanja za spremanje indeksa.')
        data = {
            'scanned_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'entries': self.entries,
        }
        try:
            write_json_atomic(path, data)
            self.index_path = path
            return True
        except OSError:
            return False

    def verify_entries_modified_and_problems(self):
        for entry in self.entries:
            filepath = entry.get('filepath')
            if not filepath:
                entry['problem'] = True
                continue
            try:
                actual_modified = os.stat(filepath).st_mtime
            except OSError:
                actual_modified = 0
            saved_modified = entry.get('modified', 0)
            actual_md5 = self.compute_md5(filepath)
            if (
                'problem' not in entry
                or actual_modified != saved_modified
                or actual_md5 != entry.get('md5')
            ):
                entry['modified'] = actual_modified
                entry['problem'] = contains_weird_chars_bytes(filepath)
                entry['md5'] = actual_md5
        self._mark_duplicates()

    def _mark_duplicates(self):
        groups = {}
        for entry in self.entries:
            entry['duplicate'] = False
            digest = entry.get('md5')
            if digest:
                groups.setdefault(digest, []).append(entry)
        for group in groups.values():
            if len(group) > 1:
                for entry in group:
                    entry['duplicate'] = True

    def refresh_folder_incremental(self, folder, update_progress=None):
        def normalized(path):
            return os.path.normcase(os.path.abspath(path))

        existing = {
            normalized(entry['filepath']): entry
            for entry in self.entries
            if entry.get('filepath')
        }
        files = list(find_cnc_files(folder))
        discovered_paths = {normalized(path) for path in files}
        added = 0
        modified = 0

        for position, path in enumerate(files, 1):
            absolute_path = os.path.abspath(path)
            try:
                modified_time = os.stat(absolute_path).st_mtime
            except OSError:
                modified_time = 0

            path_key = normalized(absolute_path)
            if path_key in existing:
                entry = existing[path_key]
                current_md5 = self.compute_md5(absolute_path)
                if (
                    entry.get('modified') != modified_time
                    or entry.get('md5') != current_md5
                ):
                    refreshed = self._new_entry(absolute_path, modified_time)
                    entry.update(refreshed)
                    modified += 1
            else:
                self.entries.append(self._new_entry(absolute_path, modified_time))
                added += 1

            if update_progress:
                update_progress(position, len(files))

        old_count = len(self.entries)
        self.entries = [
            entry for entry in self.entries
            if entry.get('filepath')
            and normalized(entry['filepath']) in discovered_paths
        ]
        removed = old_count - len(self.entries)

        self._mark_duplicates()
        self.save_index(os.path.join(folder, 'index.json'))
        return {
            'processed': len(files),
            'added': added,
            'modified': modified,
            'removed': removed,
        }
