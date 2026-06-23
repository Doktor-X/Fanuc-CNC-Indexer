# Fanuc CNC Indexer

A desktop application for indexing, searching, and managing FANUC CNC programs. Built with Python and Tkinter — no installation required beyond Python itself.

![Python](https://img.shields.io/badge/Python-3.x-blue) ![GUI](https://img.shields.io/badge/GUI-Tkinter-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

---

## Features

- 📂 **Folder scanning** — scans a folder (and subfolders) and indexes all CNC program files
- 🔍 **Search** — search by filename, program number, or program name using regex or wildcards
- 🔢 **Program number parsing** — automatically extracts O-numbers and program names from FANUC headers
- 🔁 **Incremental refresh** — updates the index without full rescanning (detects added, modified, and removed files)
- ♻️ **Duplicate detection** — finds duplicate programs using MD5 checksums
- ⚠️ **Integrity check** — flags files containing non-ASCII (problematic) characters
- 🔓 **Free O-numbers** — shows which O-numbers are available in a given range
- 🔧 **Tool tracking** — extracts required tools (T+M06) from programs; supports an external tool database (`alati.json`)
- 📱 **Android export** — exports the index as a JSON file for use on Android devices
- 🌐 **Multilingual UI** — supports Croatian and English
- 💾 **Persistent settings** — remembers last folder, column widths, sort order, ignored extensions, and language

---

## Requirements

- Python 3.x
- No external dependencies (uses only the Python standard library)

---

## Usage

```bash
python fanuc.py
```

On first launch, select a folder containing your CNC programs. The app will scan and index all files. The index is saved as `index.json` inside the scanned folder.

---

## File Structure

```
├── fanuc.py          # Entry point
├── app.py            # Main GUI application (Tkinter)
├── indexer.py        # Folder scanning and index management
├── fanuc_utils.py    # FANUC file parsing utilities
├── config_store.py   # Settings and config persistence
├── i18n.py           # Translations (HR/EN)
└── icon.ico          # Application icon
```

---

## Configuration

Settings are stored in `config.json` next to the executable. The tool database (`alati.json`) can be loaded from any location via the GUI.

---

## Contributing

Contributions are welcome! Feel free to open an issue or submit a pull request.

---

## License

This project is open source and available under the [MIT License](LICENSE).
