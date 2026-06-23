from pathlib import Path
import sys

def resource_path(filename):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = Path(__file__).resolve().parent

    return str(Path(base_path) / filename)
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from config_store import (
    load_config,
    load_setting,
    load_tools,
    set_tools_file,
    save_setting,
    save_tools,
)
from fanuc_utils import (
    contains_weird_chars_bytes,
    contains_weird_chars_content,
    extract_tools_from_program,
    load_file_content,
    parse_fanuc_header,
)
from i18n import LANGUAGES, Translator
from indexer import Indexer


def load_default_folder():
    return load_setting('default_folder')


def save_default_folder(folder):
    save_setting('default_folder', folder)


def load_column_widths_dict():
    return load_setting('column_widths', {})


def save_column_widths_dict(widths):
    save_setting('column_widths', widths)


def load_last_sort():
    return load_setting('last_sort', {})


def save_last_sort(column, reverse):
    save_setting('last_sort', {'column': column, 'reverse': reverse})


def load_column_order():
    return load_setting('column_order')


def save_column_order(order):
    save_setting('column_order', list(order) if order is not None else None)


def load_ignored_extensions():
    return load_setting('ignored_extensions', [])


def save_ignored_extensions(extensions):
    save_setting('ignored_extensions', extensions)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.iconbitmap(resource_path("icon.ico"))
        self._config = load_config()
        self.translator = Translator(self._config.get('language', 'hr'))
        self.title(self.translator('Fanuc CNC Indexer'))
        self.geometry('1100x650')
        self.minsize(900, 500)
        self.state('zoomed')   # startaj u fullscreen (Windows)
        self.option_add("*Font", ("Segoe UI", 10))

        self.indexer = Indexer()
        self.current_folder = None
        self.sort_column = None
        self.sort_reverse = False

        if 'column_widths' not in self._config:
            self._config['column_widths'] = {}

        self._scanning_thread = None
        self.create_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.auto_load_last_index()
        self.apply_last_sort()
        self.apply_column_order()

    def t(self, text, **values):
        return self.translator(text, **values)

    def _on_language_changed(self, _event=None):
        selected_name = self.language_var.get()
        language = next(
            (code for code, name in LANGUAGES.items() if name == selected_name),
            'hr',
        )
        if language == self.translator.language:
            return

        selected = self.get_selected_entry()
        selected_path = selected.get('filepath') if selected else None
        preview_content = self.txt_preview.get('1.0', 'end-1c') if selected else None
        query = self.search_var.get()
        self.save_column_settings()

        self.translator.set_language(language)
        save_setting('language', language)
        for child in self.winfo_children():
            child.destroy()

        self.create_widgets()
        self.search_var.set(query)
        self.update_list()
        self.apply_column_order()
        if selected_path and self.tree.exists(selected_path):
            self.tree.selection_set(selected_path)
            self.tree.see(selected_path)
            self.show_preview()
            self.txt_preview.delete('1.0', 'end')
            self.txt_preview.insert('1.0', preview_content)
            self.apply_highlighting()

    # ----------------- GUI -----------------
    def create_widgets(self):
        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill='x')

        left_buttons = ttk.Frame(top)
        left_buttons.pack(side='left')
        self.btn_scan = ttk.Button(left_buttons, text=self.t('Skeniraj mapu'), command=self.on_scan)
        self.btn_scan.pack(side='left')
        ttk.Button(left_buttons, text=self.t('Učitaj index.json'), command=self.on_load_index).pack(side='left', padx=(6, 0))
        ttk.Button(left_buttons, text=self.t('Učitaj bazu alata'), command=self.on_load_tools_database).pack(side='left', padx=(6, 0))
        ttk.Button(left_buttons, text=self.t('Izvoz za Android'), command=self.on_export_android).pack(side='left', padx=(6, 0))
        ttk.Button(left_buttons, text=self.t('Provjera integriteta'), command=self.show_integrity_popup).pack(side='left', padx=(6, 0))
        ttk.Button(left_buttons, text=self.t('Slobodni O-brojevi'), command=self.show_free_o_popup).pack(side='left', padx=(6, 0))
        ttk.Button(left_buttons, text=self.t('Osvježi bazu'), command=self.on_refresh_db).pack(side='left', padx=(6, 0))

        search_frame = ttk.Frame(top)
        search_frame.pack(side='left', padx=(20, 0), fill='x', expand=True)
        ttk.Label(search_frame, text=self.t('Pretraži (regex/wildcard):')).pack(side='left', padx=(0, 4))
        self.search_var = tk.StringVar()
        ent_search = ttk.Entry(search_frame, textvariable=self.search_var)
        ent_search.pack(side='left', fill='x', expand=True)
        ent_search.bind('<KeyRelease>', lambda e: self.update_list())
        ttk.Button(search_frame, text=self.t('Očisti'), command=lambda: self.search_var.set('')).pack(side='left', padx=(4, 0))

        right_top = ttk.Frame(top)
        right_top.pack(side='right')
        ttk.Label(right_top, text=self.t('Jezik:')).pack(side='left', padx=(8, 4))
        self.language_var = tk.StringVar(value=LANGUAGES[self.translator.language])
        language_box = ttk.Combobox(
            right_top,
            textvariable=self.language_var,
            values=tuple(LANGUAGES.values()),
            state='readonly',
            width=10,
        )
        language_box.pack(side='left')
        language_box.bind('<<ComboboxSelected>>', self._on_language_changed)
        ttk.Frame(right_top, width=8).pack(side='right')

        self.progress = ttk.Progressbar(top, length=220, mode='determinate')
        self.progress.pack(side='left', padx=(10, 0))
        self.progress_label = ttk.Label(top, text='')
        self.progress_label.pack(side='left', padx=(6, 0))

        main_pane = tk.PanedWindow(self, orient='horizontal', sashrelief='raised')
        main_pane.pack(fill='both', expand=True, padx=8, pady=6)
        self.main_pane = main_pane

        # -------- Treeview (Left) --------
        left_frame = ttk.Frame(main_pane)

        self.cols = ('filename', 'program_number', 'program_name', 'modified')
        self.display_names = {
            'filename': self.t('Naziv datoteke'),
            'program_number': self.t('Broj programa'),
            'program_name': self.t('Ime programa'),
            'modified': self.t('Izmijenjeno')
        }
        self.tree = ttk.Treeview(left_frame, columns=self.cols, show='tree headings', selectmode='browse')
        for c in self.cols:
            self.tree.heading(c, text=self.display_names[c], command=lambda _c=c: self.sort_treeview(_c))
            default_w = 300 if c == 'filename' else 120
            self.tree.column(c, width=default_w, anchor='w')

        saved = load_column_widths_dict()
        for c in self.cols:
            try:
                w = saved.get(c)
                if isinstance(w, int) and w > 10:
                    self.tree.column(c, width=w)
            except Exception:
                pass

        self.tree.column('#0', width=24, stretch=False)
        self.tree.heading('#0', text='')

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", rowheight=22, font=('Segoe UI', 10),
                        background="#1C1C1C", fieldbackground="#1C1C1C", foreground="white")
        style.configure("Treeview.Heading", background="#E6E6E6", foreground="black", font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[('selected', '#347083')])
        self.tree.tag_configure('oddrow', background='#252535')
        self.tree.tag_configure('evenrow', background='#1C1C2C')
        self.tree.tag_configure('problem', background='#8B0000')
        self.tree.tag_configure('duplicate', background='#0A7F2F')
        self.tree.tag_configure('dup_master', background='#0A7F2F', font=('Segoe UI', 10, 'bold'))

        scroll_tree = ttk.Scrollbar(left_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_tree.set)

        left_frame.rowconfigure(0, weight=1)
        left_frame.columnconfigure(0, weight=1)

        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll_tree.grid(row=0, column=1, sticky='ns')

        # dvoklik otvara program u editoru
        self.tree.bind('<Double-1>', lambda e: self.on_open_selected())

        self.tree.bind('<<TreeviewSelect>>', lambda e: self.show_preview())

        main_pane.add(left_frame, minsize=600)

        # -------- Preview (Right) --------
        right_frame = ttk.Frame(main_pane)
        self.header_frame = ttk.Frame(right_frame, height=100)
        self.header_frame.pack(fill='x')
        self.lbl_header = tk.Label(self.header_frame, text="", justify='left', anchor='w',
                                   font=('Segoe UI', 9, 'bold'),
                                   background="#0B0B3B", foreground="white")
        self.lbl_header.pack(fill='x', padx=2, pady=2)

        content_frame = ttk.Frame(right_frame)
        content_frame.pack(fill='both', expand=True)

        self.txt_preview = tk.Text(content_frame, wrap='none', font=('Courier', 10),
                                   bg='#0B0B3B', fg='white', insertbackground='white')
        scroll_preview_y = ttk.Scrollbar(content_frame, orient='vertical', command=self.txt_preview.yview)
        self.txt_preview.configure(yscrollcommand=scroll_preview_y.set)

        content_frame.rowconfigure(0, weight=1)
        content_frame.columnconfigure(0, weight=1)

        self.txt_preview.grid(row=0, column=0, sticky='nsew')
        scroll_preview_y.grid(row=0, column=1, sticky='ns')

        bottom_bar = ttk.Frame(right_frame)
        bottom_bar.pack(side='bottom', fill='x')

        scroll_preview_x = ttk.Scrollbar(bottom_bar, orient='horizontal', command=self.txt_preview.xview)
        scroll_preview_x.pack(side='bottom', fill='x')
        self.txt_preview.configure(xscrollcommand=scroll_preview_x.set)

        bottom_buttons = ttk.Frame(bottom_bar, padding=(0, 4))
        bottom_buttons.pack(side='top', fill='x')

        ttk.Button(bottom_buttons, text=self.t('Spremi izmjene'), command=self.on_save_preview).pack(side='left', padx=(6, 0))
        ttk.Button(bottom_buttons, text=self.t('Potrebni alati'), command=self.show_tools_for_current_program).pack(side='left', padx=(6, 0))
        ttk.Button(bottom_buttons, text=self.t('Svi alati'), command=self.show_all_tools_popup).pack(side='left', padx=(6, 0))
        ttk.Button(bottom_buttons, text=self.t('Obriši datoteku'), command=self._delete_selected_file).pack(side='right')

        self.txt_preview.bind("<KeyRelease>", lambda e: self.apply_highlighting())

        main_pane.add(right_frame, minsize=600)

        # Status bar
        status_frame = ttk.Frame(self, padding=(8, 4))
        status_frame.pack(fill='x', side='bottom')
        self.status_var = tk.StringVar(value=self.t(
            'Ispravni: {valid} | Unikatni: {unique} | Duplikati: {duplicates} | Loši: {bad}',
            valid=0, unique=0, duplicates=0, bad=0,
        ))
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, anchor='w')
        self.status_label.pack(side='left')

        # Gumb za ekstenzije - dolje lijevo
        ttk.Button(status_frame, text=self.t('Ekstenzije...'), command=self.show_ignore_ext_popup).pack(side='left', padx=(10, 0))
        ttk.Button(status_frame, text=self.t('Otvori u editoru'), command=self.on_open_selected).pack(side='left', padx=(10, 0))

        self.tree.bind('<ButtonRelease-1>', lambda e: self.save_column_settings())

    # ---------- Helpers ----------
    def _delete_selected_file(self):
        e = self.get_selected_entry()
        if not e:
            messagebox.showwarning(self.t('Nije odabrano'), self.t('Odaberite datoteku za brisanje.'))
            return
        confirm = messagebox.askyesno(
            self.t('Potvrda'),
            self.t("Sigurno želite obrisati '{filename}'?", filename=e['filename']),
        )
        if not confirm:
            return
        try:
            if os.path.exists(e['filepath']):
                os.remove(e['filepath'])
            self.indexer.entries = [en for en in self.indexer.entries if en.get('filepath') != e.get('filepath')]
            if self.current_folder:
                try:
                    self.indexer.save_index(os.path.join(self.current_folder, 'index.json'))
                except Exception:
                    pass
            self.update_list()
            self.txt_preview.delete('1.0', 'end')
            self.lbl_header.config(text="")
            messagebox.showinfo(
                self.t('Obrisano'),
                self.t("Datoteka '{filename}' obrisana.", filename=e['filename']),
            )
        except Exception as ex:
            messagebox.showerror(
                self.t('Greška'), self.t('Ne mogu obrisati datoteku: {error}', error=ex)
            )

    def save_column_settings(self):
        try:
            widths = {c: int(self.tree.column(c)['width']) for c in self.cols}
            save_column_widths_dict(widths)
            save_column_order(self.tree['displaycolumns'])
        except Exception:
            pass

    def apply_column_order(self):
        order = load_column_order()
        if order:
            try:
                self.tree['displaycolumns'] = order
            except Exception:
                pass

    def _save_tree_state(self):
        try:
            disp = tuple(self.tree['displaycolumns'])
        except Exception:
            disp = tuple(self.cols)
        try:
            yview = self.tree.yview()
        except Exception:
            yview = (0.0, 1.0)
        try:
            xview = self.tree.xview()
        except Exception:
            xview = (0.0, 1.0)
        try:
            sel = self.tree.selection()
            sel0 = sel[0] if sel else None
        except Exception:
            sel0 = None

        sort_col = getattr(self, 'sort_column', None)
        sort_rev = getattr(self, 'sort_reverse', False)

        self._saved_tree_state = {
            'displaycolumns': disp,
            'yview': yview,
            'xview': xview,
            'selected': sel0,
            'sort_column': sort_col,
            'sort_reverse': sort_rev
        }

    def _restore_tree_state(self):
        st = getattr(self, '_saved_tree_state', None)
        if not st:
            return

        def _do_restore():
            try:
                self.tree['displaycolumns'] = st.get('displaycolumns', self.cols)
            except Exception:
                pass

            sort_col = st.get('sort_column')
            sort_rev = st.get('sort_reverse', False)
            try:
                if sort_col and sort_col in self.cols:
                    self.sort_treeview(sort_col, sort_rev)
            except Exception:
                pass

            try:
                yf = st.get('yview', (0.0, 1.0))[0]
                yf = max(0.0, min(1.0, float(yf)))
                self.tree.yview_moveto(yf)
            except Exception:
                pass
            try:
                xf = st.get('xview', (0.0, 1.0))[0]
                xf = max(0.0, min(1.0, float(xf)))
                self.tree.xview_moveto(xf)
            except Exception:
                pass
            try:
                sel = st.get('selected')
                if sel:
                    self.tree.selection_set(sel)
                    self.tree.see(sel)
            except Exception:
                pass
            try:
                del self._saved_tree_state
            except Exception:
                self._saved_tree_state = None

        self.after(50, _do_restore)

    # ---------- Ekstenzije popup ----------
    def show_ignore_ext_popup(self):
        popup = tk.Toplevel(self)
        popup.title(self.t('Ignorirane ekstenzije'))
        popup.geometry('400x160')

        frame = ttk.Frame(popup, padding=8)
        frame.pack(fill='both', expand=True)

        ttk.Label(
            frame,
            text=self.t('Ekstenzije za ignorirati (odvojene zarezom, točkom-zarezom ili razmakom):')
        ).pack(anchor='w')

        current_exts = load_ignored_extensions()
        var = tk.StringVar(value=', '.join(current_exts))

        entry = ttk.Entry(frame, textvariable=var)
        entry.pack(fill='x', pady=(4, 8))

        ttk.Label(
            frame,
            text=self.t('Promjene vrijede pri sljedećem skeniranju ili osvježavanju baze.'),
            foreground='gray'
        ).pack(anchor='w', pady=(0, 8))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill='x')

        def save_and_close():
            text = var.get().strip()
            if not text:
                exts = []
            else:
                parts = re.split(r'[;, ]+', text)
                exts = []
                for p in parts:
                    p = p.strip()
                    if not p:
                        continue
                    if not p.startswith('.'):
                        p = '.' + p
                    exts.append(p.lower())
            # ako je prazno, znači "ne ignoriraj ništa"
            if not exts:
                exts = []
            save_ignored_extensions(exts)
            messagebox.showinfo(
                self.t('Spremljeno'),
                self.t(
                    'Ekstenzije su spremljene.\n'
                    'Ponovno skeniraj ili osvježi bazu da se promjene primijene.'
                ),
            )
            popup.destroy()

        ttk.Button(btn_frame, text=self.t('Spremi'), command=save_and_close).pack(side='right', padx=(4, 0))
        ttk.Button(btn_frame, text=self.t('Odustani'), command=popup.destroy).pack(side='right')

    # ---------- Default folder ----------
    def auto_load_last_index(self):
        folder = load_default_folder()
        if folder and os.path.exists(os.path.join(folder, 'index.json')):
            try:
                self.current_folder = folder
                self.indexer.load_index(os.path.join(folder, 'index.json'))
                self.indexer.verify_entries_modified_and_problems()
                try:
                    self.indexer.save_index(os.path.join(folder, 'index.json'))
                except Exception:
                    pass
                self.update_list()
            except Exception as e:
                messagebox.showerror(
                    self.t('Greška'), self.t('Neuspjelo učitavanje indeksa: {error}', error=e)
                )

    # ---------- Lista / Sort ----------
    def sort_treeview(self, col, reverse=False):
        items = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]
        try:
            items.sort(key=lambda t: float(t[0]) if t[0] != "" else 0, reverse=reverse)
        except Exception:
            items.sort(key=lambda t: t[0].lower(), reverse=reverse)
        for index, (_, item_id) in enumerate(items):
            self.tree.move(item_id, "", index)
        for c in self.cols:
            text = self.display_names.get(c, c)
            arrow = ""
            if c == col:
                arrow = " ↓" if reverse else " ↑"
            self.tree.heading(
                c,
                text=text + arrow,
                command=lambda _c=c, _r=not reverse: self.sort_treeview(_c, _r)
            )
        self.sort_column = col
        self.sort_reverse = reverse
        save_last_sort(col, reverse)

    def apply_last_sort(self):
        last_sort = load_last_sort()
        col = last_sort.get('column')
        rev = last_sort.get('reverse', False)
        if col and col in self.cols:
            self.sort_treeview(col, rev)

    def _make_query_regex(self, query):
        q = (query or '').strip()
        if not q:
            return None
        if any(ch in q for ch in '*?'):
            q_escaped = re.escape(q)
            q_escaped = q_escaped.replace(r"\*", ".*").replace(r"\?", ".")
            return re.compile(q_escaped, re.IGNORECASE)
        try:
            return re.compile(q, re.IGNORECASE)
        except re.error:
            return re.compile(re.escape(q), re.IGNORECASE)

    def update_list(self):
        query = (self.search_var.get() or '').strip()
        qre = self._make_query_regex(query)

        for r in self.tree.get_children():
            self.tree.delete(r)

        entries = self.indexer.entries

        valid = 0
        unique = 0
        bad = 0
        duplicates = 0
        for e in entries:
            if e.get('problem'):
                bad += 1
            else:
                valid += 1
                if e.get('duplicate'):
                    duplicates += 1
                else:
                    unique += 1

        md5_groups = {}
        for e in entries:
            md5 = e.get('md5')
            if e.get('duplicate') and md5:
                md5_groups.setdefault(md5, []).append(e)

        duplicate_md5s = set(md5_groups.keys())
        display_index = 0

        # 1) normalni zapisi
        for e in entries:
            md5 = e.get('md5')
            if e.get('duplicate') and md5 in duplicate_md5s:
                continue

            hay = (e.get('filename') or '') + ' ' + (e.get('program_number') or '') + ' ' + (e.get('program_name') or '')
            if qre and not qre.search(hay):
                continue

            try:
                if e.get('modified'):
                    mod = datetime.datetime.fromtimestamp(e.get('modified', 0)).strftime('%Y-%m-%d %H:%M')
                else:
                    mod = ''
            except Exception:
                mod = ''

            iid = e['filepath']
            if e.get('problem', False):
                tag = 'problem'
            else:
                tag = 'evenrow' if display_index % 2 == 0 else 'oddrow'

            self.tree.insert(
                '',
                'end',
                iid=iid,
                text='',
                values=(e.get('filename'),
                        e.get('program_number') or '',
                        e.get('program_name') or '',
                        mod),
                tags=(tag,)
            )
            display_index += 1

        # 2) grupe duplikata
        for md5, group in md5_groups.items():
            if not group:
                continue

            if qre:
                any_match = False
                for g in group:
                    hay = (g.get('filename') or '') + ' ' + (g.get('program_number') or '') + ' ' + (g.get('program_name') or '')
                    if qre.search(hay):
                        any_match = True
                        break
                if not any_match:
                    continue

            master = sorted(group, key=lambda e: (e.get('modified', 0), e.get('filename') or ''))[0]

            try:
                if master.get('modified'):
                    mod_master = datetime.datetime.fromtimestamp(master.get('modified', 0)).strftime('%Y-%m-%d %H:%M')
                else:
                    mod_master = ''
            except Exception:
                mod_master = ''

            parent_id = master['filepath']
            if master.get('problem', False):
                master_tag = 'problem'
            else:
                master_tag = 'dup_master'

            self.tree.insert(
                '',
                'end',
                iid=parent_id,
                text='',
                values=(master.get('filename'),
                        master.get('program_number') or '',
                        master.get('program_name') or '',
                        mod_master),
                tags=(master_tag,)
            )
            display_index += 1

            for child in group:
                if child is master:
                    continue
                try:
                    if child.get('modified'):
                        mod_child = datetime.datetime.fromtimestamp(child.get('modified', 0)).strftime('%Y-%m-%d %H:%M')
                    else:
                        mod_child = ''
                except Exception:
                    mod_child = ''

                child_id = child['filepath']
                if child.get('problem', False):
                    ctag = 'problem'
                else:
                    ctag = 'duplicate'

                self.tree.insert(
                    parent_id,
                    'end',
                    iid=child_id,
                    text='',
                    values=(child.get('filename'),
                            child.get('program_number') or '',
                            child.get('program_name') or '',
                            mod_child),
                    tags=(ctag,)
                )

        try:
            self.status_var.set(self.t(
                'Ispravni: {valid} | Unikatni: {unique} | Duplikati: {duplicates} | Loši: {bad}',
                valid=valid, unique=unique, duplicates=duplicates, bad=bad,
            ))
        except Exception:
            pass

    def get_selected_entry(self):
        sel = self.tree.selection()
        if not sel:
            return None
        fid = sel[0]
        for e in self.indexer.entries:
            if e['filepath'] == fid:
                return e
        return None

    # ---------- Preview ----------
    def show_preview(self):
        e = self.get_selected_entry()
        if not e:
            self.lbl_header.config(text="")
            self.txt_preview.delete('1.0', 'end')
            return
        try:
            if e.get('modified'):
                mod_text = datetime.datetime.fromtimestamp(e.get('modified', 0)).strftime('%Y-%m-%d %H:%M')
            else:
                mod_text = ''
        except Exception:
            mod_text = ''
        header_text = self.t(
            'Datoteka: {filename}\nProgram broj: {number}\nIme programa: {name}\n'
            'Izmjena: {modified}\nMD5: {md5}\nLokacija: {path}',
            filename=os.path.basename(e['filepath']),
            number=e.get('program_number', ''),
            name=e.get('program_name', ''),
            modified=mod_text,
            md5=e.get('md5', '') or '',
            path=e['filepath'],
        )
        self.lbl_header.config(text=header_text)
        self.txt_preview.delete('1.0', 'end')
        content = load_file_content(e['filepath'])
        self.txt_preview.insert('1.0', content)
        self.apply_highlighting()

    def apply_highlighting(self):
        txt = self.txt_preview
        content = txt.get("1.0", "end-1c")
        for tag in txt.tag_names():
            txt.tag_remove(tag, "1.0", "end")
        pattern = r"\([^)]*\)"
        for match in re.finditer(pattern, content, re.IGNORECASE):
            start = f"1.0+{match.start()}c"
            end = f"1.0+{match.end()}c"
            txt.tag_add("comment", start, end)
        txt.tag_config("comment", foreground="#32CD32")

    # ---------- Otvori / save ----------
    def on_open_file(self, event=None):
        self.on_open_selected()

    def on_open_selected(self):
        e = self.get_selected_entry()
        if not e:
            messagebox.showwarning(self.t('Nije odabrano'), self.t('Odaberite datoteku.'))
            return
        path = e['filepath']
        if not os.path.exists(path):
            messagebox.showerror(self.t('Nema datoteke'), self.t('Datoteka ne postoji: {path}', path=path))
            return
        try:
            if sys.platform.startswith('darwin'):
                subprocess.call(('open', path))
            elif os.name == 'nt':
                os.startfile(path)
            elif os.name == 'posix':
                subprocess.call(('xdg-open', path))
        except Exception as ex:
            messagebox.showerror(self.t('Greška'), self.t('Neuspješno otvaranje: {error}', error=ex))

    def on_rescan_selected(self):
        e = self.get_selected_entry()
        if not e:
            messagebox.showwarning(self.t('Nije odabrano'), self.t('Odaberite datoteku.'))
            return
        info = parse_fanuc_header(e['filepath'])
        e['program_number'] = info['program_number']
        e['program_name'] = info['program_name']
        try:
            stat = os.stat(e['filepath'])
            e['modified'] = stat.st_mtime
            e['problem'] = contains_weird_chars_bytes(e['filepath'])
            e['md5'] = self.indexer.compute_md5(e['filepath'])
        except Exception:
            e['problem'] = True
            e['modified'] = e.get('modified', 0)
            e['md5'] = None
        self.indexer._mark_duplicates()
        if self.current_folder:
            try:
                self.indexer.save_index(os.path.join(self.current_folder, 'index.json'))
            except Exception:
                pass
        messagebox.showinfo(self.t('Ponovno skenirano'), self.t('Ažurirani podaci za odabranu datoteku.'))
        self.update_list()
        self.show_preview()

    def on_save_preview(self):
        e = self.get_selected_entry()
        if not e:
            messagebox.showwarning(self.t('Nije odabrano'), self.t('Odaberite datoteku za spremanje.'))
            return
        path = e['filepath']
        try:
            try:
                self._save_tree_state()
            except Exception:
                pass

            content = self.txt_preview.get("1.0", "end-1c")
            dirn = os.path.dirname(path)
            with tempfile.NamedTemporaryFile('w', delete=False, dir=dirn, encoding='utf-8') as tf:
                tf.write(content)
                tmpname = tf.name
            shutil.move(tmpname, path)
            try:
                stat = os.stat(path)
                e['modified'] = stat.st_mtime
                e['problem'] = contains_weird_chars_content(content)
            except Exception:
                e['problem'] = True
            e['md5'] = self.indexer.compute_md5(path)
            info = parse_fanuc_header(path)
            e['program_number'] = info['program_number']
            e['program_name'] = info['program_name']
            self.indexer._mark_duplicates()
            if self.current_folder:
                try:
                    self.indexer.save_index(os.path.join(self.current_folder, 'index.json'))
                except Exception:
                    pass
            messagebox.showinfo(self.t('Spremanje'), self.t('Izmjene spremljene u datoteku.'))
            self.update_list()
            try:
                self._restore_tree_state()
            except Exception:
                pass
            self.show_preview()
        except Exception as ex:
            messagebox.showerror(self.t('Greška'), self.t('Neuspješno spremanje: {error}', error=ex))

    # ---------- Scan / Load / Export / Close ----------
    def _scan_thread_target(self, folder):
        def wrapped_update_progress(current, total):
            self.after(0, lambda: self._update_progress_gui(current, total))
        try:
            total = self.indexer.scan_folder(folder, update_progress=wrapped_update_progress)
            self.indexer.index_path = os.path.join(folder, 'index.json')
            self.after(0, lambda: self._on_scan_finished(folder, total))
        except Exception as ex:
            self.after(
                0,
                lambda error=ex: messagebox.showerror(
                    self.t('Greška'), self.t('Greška pri skeniranju: {error}', error=error)
                ),
            )
            self.after(0, lambda: self._scan_cleanup())

    def _refresh_thread_target(self, folder):
        def wrapped_update_progress(current, total):
            self.after(0, lambda: self._update_progress_gui(current, total))
        try:
            stats = self.indexer.refresh_folder_incremental(folder, update_progress=wrapped_update_progress)
            self.after(0, lambda: self._on_refresh_finished(folder, stats))
        except Exception as ex:
            self.after(
                0,
                lambda error=ex: messagebox.showerror(
                    self.t('Greška'), self.t('Greška pri osvježavanju: {error}', error=error)
                ),
            )
            self.after(0, lambda: self._scan_cleanup())

    def _update_progress_gui(self, current, total):
        try:
            self.progress['maximum'] = total if total > 0 else 1
            self.progress['value'] = current
            pct = int((current / float(total)) * 100) if total else 0
            self.progress_label.config(text=f"{current}/{total} ({pct}%)")
            self.update_idletasks()
        except Exception:
            pass

    def _on_scan_finished(self, folder, total):
        self.progress['value'] = 0
        self.progress_label.config(text='')
        self.update_list()
        try:
            self._restore_tree_state()
        except Exception:
            pass
        save_default_folder(folder)
        messagebox.showinfo(
            self.t('Skeniranje završeno'),
            self.t(
                'Skenirana mapa: {folder}\nPronađeno datoteka: {count}',
                folder=folder, count=len(self.indexer.entries),
            ),
        )
        self._scan_cleanup()

    def _on_refresh_finished(self, folder, stats):
        self.progress['value'] = 0
        self.progress_label.config(text='')
        self.update_list()
        try:
            self._restore_tree_state()
        except Exception:
            pass
        save_default_folder(folder)

        added = stats.get('added', 0)
        modified = stats.get('modified', 0)
        removed = stats.get('removed', 0)

        messagebox.showinfo(
            self.t('Osvježavanje završeno'),
            self.t(
                'Osvježena mapa: {folder}\nDodano datoteka: {added}\n'
                'Izmijenjeno datoteka: {modified}\n'
                'Uklonjeno iz baze (ne postoji ili je zanemareno): {removed}',
                folder=folder, added=added, modified=modified, removed=removed,
            ),
        )
        self._scan_cleanup()

    def _scan_cleanup(self):
        self._scanning_thread = None
        self.btn_scan.config(state='normal')

    def on_scan(self):
        if self._scanning_thread and self._scanning_thread.is_alive():
            messagebox.showinfo(self.t('Skeniranje u tijeku'), self.t('Skeniranje već traje.'))
            return
        folder = filedialog.askdirectory(title=self.t('Odaberite mapu za skeniranje'))
        if not folder:
            return
        self.current_folder = folder
        self.progress['value'] = 0
        self.progress_label.config(text='0/0 (0%)')
        self.update_idletasks()
        self.btn_scan.config(state='disabled')
        try:
            self._save_tree_state()
        except Exception:
            pass
        t = threading.Thread(target=self._scan_thread_target, args=(folder,), daemon=True)
        self._scanning_thread = t
        t.start()

    def on_load_index(self):
        path = filedialog.askopenfilename(
            title=self.t('Odaberi index.json'),
            filetypes=[(self.t('JSON datoteke'), '*.json'), (self.t('Sve datoteke'), '*.*')]
        )
        if not path:
            return
        try:
            try:
                self._save_tree_state()
            except Exception:
                pass

            self.indexer.load_index(path)
            self.current_folder = os.path.dirname(path)
            self.indexer.verify_entries_modified_and_problems()
            try:
                self.indexer.save_index(path)
            except Exception:
                pass
            messagebox.showinfo(
                self.t('Index učitan'),
                self.t(
                    'Učitan index: {path}\nBroj unosa: {count}',
                    path=path, count=len(self.indexer.entries),
                ),
            )
            self.update_list()
            try:
                self._restore_tree_state()
            except Exception:
                pass
            save_default_folder(self.current_folder)
        except Exception as e:
            messagebox.showerror(self.t('Greška'), self.t('Neuspjelo učitavanje indeksa: {error}', error=e))

    def on_load_tools_database(self):
        path = filedialog.askopenfilename(
            title=self.t('Odaberi bazu alata'),
            filetypes=[
                (self.t('JSON datoteke'), '*.json'),
                (self.t('Sve datoteke'), '*.*'),
            ],
        )
        if not path:
            return

        try:
            with open(path, 'r', encoding='utf-8') as tools_file:
                tools = json.load(tools_file)
            if not isinstance(tools, dict):
                raise ValueError(self.t('Baza alata mora sadržavati JSON objekt.'))
            set_tools_file(path)
            messagebox.showinfo(
                self.t('Baza alata učitana'),
                self.t(
                    'Učitana baza alata:\n{path}\n\nAlata: {count}',
                    path=path,
                    count=len(tools),
                ),
            )
        except (OSError, json.JSONDecodeError, ValueError) as error:
            messagebox.showerror(
                self.t('Greška'),
                self.t('Neuspjelo učitavanje baze alata:\n{error}', error=error),
            )
            
    def on_export_android(self):
        if not self.indexer.entries:
            messagebox.showwarning(
                self.t('Nema podataka'),
                self.t('Indeks je prazan. Prvo skeniraj mapu.')
            )
            return

        path = filedialog.asksaveasfilename(
            title=self.t('Spremi Android index'),
            defaultextension='.json',
            initialfile='index_android.json',
            filetypes=[
                (self.t('JSON datoteke'), '*.json'),
                (self.t('Sve datoteke'), '*.*')
            ]
        )

        if not path:
            return

        try:
            # Učitaj ignorirane ekstenzije
            ignored_exts = set(ext.lower() for ext in load_ignored_extensions())

            android_entries = []

            # Filtriraj entitete – preskoči ignorirane ekstenzije
            entries_to_export = []
            skipped = 0
            for e in self.indexer.entries:
                fp = e.get('filepath') or ''
                ext = os.path.splitext(fp)[1].lower()
                if ignored_exts and ext in ignored_exts:
                    skipped += 1
                    continue
                entries_to_export.append(e)

            total = len(entries_to_export)
            self.progress['maximum'] = max(total, 1)
            self.progress['value'] = 0
            self.progress_label.config(text=f'0/{total} (0%)')
            self.update_idletasks()

            for position, e in enumerate(entries_to_export, 1):
                tools = []
                fp = e.get('filepath')

                if fp and os.path.exists(fp):
                    try:
                        content = load_file_content(fp)
                        tools = extract_tools_from_program(content)
                    except Exception:
                        tools = []

                android_entries.append({
                    'filename': e.get('filename'),
                    'program_number': e.get('program_number'),
                    'program_name': e.get('program_name'),
                    'tools': tools,
                    'modified': e.get('modified', 0),
                    'problem': e.get('problem', False),
                    'duplicate': e.get('duplicate', False),
                    'md5': e.get('md5') or None,
                })

                percentage = int((position / total) * 100)
                self.progress['value'] = position
                self.progress_label.config(
                    text=f'{position}/{total} ({percentage}%)'
                )
                self.update_idletasks()

            data = {
                'created_at': datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat(),
                'entries': android_entries
            }

            with open(path, 'w', encoding='utf-8') as f:
                json.dump(
                    data,
                    f,
                    ensure_ascii=False,
                    indent=2
                )

            msg = self.t(
                'Android index spremljen:\n{path}\n\nPrograma: {count}',
                path=path, count=len(android_entries),
            )
            if skipped > 0:
                msg += self.t(
                    '\nPreskočeno (ignorirane ekstenzije): {count}',
                    count=skipped,
                )
            messagebox.showinfo(self.t('Izvoz završen'), msg)

        except Exception as ex:
            messagebox.showerror(
                self.t('Greška'),
                self.t('Neuspješan Android izvoz:\n{error}', error=ex),
            )
        finally:
            self.progress['value'] = 0
            self.progress_label.config(text='')
            

    def _on_close(self):
        try:
            widths = {c: int(self.tree.column(c)['width']) for c in self.cols}
            save_column_widths_dict(widths)
            save_column_order(self.tree['displaycolumns'])
        except Exception:
            pass
        self.destroy()

    # ---------- Integrity ----------
    def is_problematic(self, entry):
        return bool(entry.get('problem', False))

    def mark_problematic_entries(self):
        for e in self.indexer.entries:
            if 'problem' not in e:
                try:
                    e['problem'] = contains_weird_chars_bytes(e['filepath'])
                except Exception:
                    e['problem'] = True

    def show_integrity_popup(self):
        popup = tk.Toplevel(self)
        popup.title(self.t('Provjera integriteta'))
        popup.geometry('700x400')

        frame = ttk.Frame(popup)
        frame.pack(fill='both', expand=True)

        bad_total = sum(1 for e in self.indexer.entries if e.get('problem'))
        label_var = tk.StringVar(value=self.t('Loših programa: {count}', count=bad_total))
        lbl_bad = ttk.Label(frame, textvariable=label_var, foreground='red')
        lbl_bad.pack(anchor='w', padx=4, pady=(4, 2))

        tree = ttk.Treeview(
            frame,
            columns=('filename', 'program_number', 'program_name', 'problem', 'filepath'),
            show='headings',
            selectmode='extended'
        )
        tree.heading('filename', text=self.t('Naziv datoteke'))
        tree.heading('program_number', text=self.t('Broj programa'))
        tree.heading('program_name', text=self.t('Ime programa'))
        tree.heading('problem', text=self.t('Problem'))
        tree.heading('filepath', text='')

        tree.column('filename', width=300)
        tree.column('program_number', width=120)
        tree.column('program_name', width=250)
        tree.column('problem', width=80, anchor='center')
        tree.column('filepath', width=0, stretch=False)

        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        tree.pack(fill='both', expand=True)

        for e in self.indexer.entries:
            if e.get('problem'):
                tree.insert(
                    '',
                    'end',
                    values=(
                        e.get('filename'),
                        e.get('program_number') or '',
                        e.get('program_name') or '',
                        self.t('DA'),
                        e.get('filepath') or ''
                    )
                )

        def delete_selected():
            sel = tree.selection()
            if not sel:
                return
            confirm = messagebox.askyesno(
                self.t('Potvrda'),
                self.t('Sigurno želite obrisati {count} datoteka?', count=len(sel)),
            )
            if not confirm:
                return

            to_delete_entries = []
            errors = []

            for iid in sel:
                vals = tree.item(iid, 'values')
                filename = vals[0]
                filepath = vals[4]

                entry = next((e for e in self.indexer.entries if e.get('filepath') == filepath), None)
                if entry and filepath and os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                        to_delete_entries.append(entry)
                    except Exception as ex:
                        errors.append(self.t('Ne mogu obrisati {filename}: {error}', filename=filename, error=ex))

            for e in to_delete_entries:
                try:
                    self.indexer.entries.remove(e)
                except ValueError:
                    pass

            if self.current_folder:
                try:
                    self.indexer.save_index(os.path.join(self.current_folder, 'index.json'))
                except Exception:
                    pass

            for iid in sel:
                try:
                    tree.delete(iid)
                except Exception:
                    pass

            new_bad = sum(1 for e in self.indexer.entries if e.get('problem'))
            label_var.set(self.t('Loših programa: {count}', count=new_bad))
            self.update_list()

            msg = self.t('Obrisano {count} datoteka.', count=len(to_delete_entries))
            if errors:
                msg += self.t('\n\nProblemi:\n') + "\n".join(errors)
            messagebox.showinfo(self.t('Obrisano'), msg)

        btn_frame = ttk.Frame(popup)
        btn_frame.pack(fill='x', pady=4)
        ttk.Button(btn_frame, text=self.t('Obriši odabrane datoteke'), command=delete_selected).pack(side='left', padx=4)
        ttk.Button(btn_frame, text=self.t('Zatvori'), command=popup.destroy).pack(side='right', padx=4)

    # ---------- Slobodni O-brojevi ----------
    def show_free_o_popup(self):
        popup = tk.Toplevel(self)
        popup.title(self.t('Slobodni O-brojevi'))
        popup.geometry('400x500')

        used = set()
        for e in self.indexer.entries:
            if e.get('program_number'):
                try:
                    used.add(int(e.get('program_number')))
                except Exception:
                    pass

        free_count = sum(1 for i in range(0, 10000) if i not in used)

        header = ttk.Frame(popup)
        header.pack(fill='x', padx=4, pady=(6, 2))
        lbl_free = tk.Label(
            header,
            text=self.t('Slobodnih O-brojeva: {count}', count=free_count),
            fg='green',
            anchor='w',
        )
        lbl_free.pack(side='left', fill='x')

        frame = ttk.Frame(popup)
        frame.pack(fill='both', expand=True)

        tree = ttk.Treeview(frame, columns=('o_number',), show='tree')
        tree.heading('#0', text=self.t('Slobodni O-brojevi'))
        tree.column('#0', width=200)

        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        tree.pack(fill='both', expand=True)

        def add_range(parent_id, start, end):
            for i in range(start, end):
                if i not in used:
                    tree.insert(parent_id, 'end', text=f"O{i:04d}")

        for start in range(0, 1000, 100):
            parent = tree.insert('', 'end', text=f"{start}-{start + 100 - 1}")
            add_range(parent, start, start + 100)

        for start in range(1000, 10000, 1000):
            end = min(start + 1000, 10000)
            parent = tree.insert('', 'end', text=f"{start}-{end - 1}")
            add_range(parent, start, end)

    # ---------- Refresh DB ----------
    def on_refresh_db(self):
        if not self.current_folder:
            messagebox.showwarning(
                self.t('Nije odabrano'),
                self.t('Prvo učitaj index ili skeniraj mapu kako bi se znala radna mapa.'),
            )
            return
        if self._scanning_thread and self._scanning_thread.is_alive():
            messagebox.showinfo(self.t('U tijeku'), self.t('Druga operacija je već u tijeku.'))
            return
        self.btn_scan.config(state='disabled')
        try:
            self._save_tree_state()
        except Exception:
            pass
        t = threading.Thread(target=self._refresh_thread_target, args=(self.current_folder,), daemon=True)
        self._scanning_thread = t
        t.start()
    
    def show_tools_for_current_program(self):
        e = self.get_selected_entry()
        if not e:
            messagebox.showwarning(self.t('Nije odabrano'), self.t('Odaberite program.'))
            return

        content = load_file_content(e['filepath'])
        tools = extract_tools_from_program(content)
        tools_map = load_tools()

        popup = tk.Toplevel(self)
        popup.title(self.t('Potrebni alati'))
        popup.geometry('500x400')

        frame = ttk.Frame(popup, padding=6)
        frame.pack(fill='both', expand=True)

        ttk.Label(frame, text=self.t('Program: {filename}', filename=e.get('filename')), font=('Segoe UI', 10, 'bold')).pack(anchor='w')
        ttk.Label(frame, text=self.t('Ukupno alata: {count}', count=len(tools))).pack(anchor='w', pady=(0, 6))

        tree = ttk.Treeview(frame, columns=('t', 'name'), show='headings')
        tree.heading('t', text=self.t('T-broj'))
        tree.heading('name', text=self.t('Naziv alata'))

        tree.column('t', width=80, anchor='center')
        tree.column('name', width=300)

        tree.pack(fill='both', expand=True)

        for tnum in tools:
            key = f"T{tnum}"
            name = tools_map.get(key) or self.t('⚠️ Nije definirano')
            tree.insert('', 'end', values=(f"T{tnum}", name))
            
    def show_all_tools_popup(self):
        tools_map = load_tools()

        popup = tk.Toplevel(self)
        popup.title(self.t('Svi alati – pretraživanje / uređivanje'))
        popup.geometry('650x520')

        frame = ttk.Frame(popup, padding=6)
        frame.pack(fill='both', expand=True)

        ttk.Label(frame, text=self.t('Svi alati (alati.json) – pretraživanje i uređivanje'), font=('Segoe UI', 10, 'bold')).pack(anchor='w')

        # -------- SEARCH --------
        search_frame = ttk.Frame(frame)
        search_frame.pack(fill='x', pady=(6, 4))

        ttk.Label(search_frame, text=self.t('Pretraži (regex / wildcard):')).pack(side='left')
        search_var = tk.StringVar()
        ent = ttk.Entry(search_frame, textvariable=search_var)
        ent.pack(side='left', fill='x', expand=True, padx=(6, 4))
        ttk.Button(search_frame, text=self.t('Očisti'), command=lambda: search_var.set("")).pack(side='left')

        # -------- TREE --------
        tree = ttk.Treeview(frame, columns=('t', 'name'), show='headings')
        tree.heading('t', text=self.t('T-broj'))
        tree.heading('name', text=self.t('Naziv alata'))
        tree.column('t', width=80, anchor='center')
        tree.column('name', width=420)

        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)

        tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')

        def make_regex(q):
            q = (q or '').strip()
            if not q:
                return None
            if any(ch in q for ch in '*?'):
                q = re.escape(q)
                q = q.replace(r"\*", ".*").replace(r"\?", ".")
            try:
                return re.compile(q, re.IGNORECASE)
            except re.error:
                return re.compile(re.escape(q), re.IGNORECASE)

        all_rows = []
        for key, name in tools_map.items():
            all_rows.append((key, name))

        all_rows.sort(key=lambda x: int(re.sub(r'\D+', '', x[0])) if re.sub(r'\D+', '', x[0]) else 999999)

        def refresh_list(*args):
            qre = make_regex(search_var.get())
            for r in tree.get_children():
                tree.delete(r)

            for t, name in all_rows:
                hay = f"{t} {name}"
                if qre and not qre.search(hay):
                    continue
                tree.insert('', 'end', values=(t, name))

        def save_tools_json():
            try:
                save_tools(tools_map)
                messagebox.showinfo(self.t('Spremljeno'), self.t('alati.json je ažuriran.'))
            except OSError as ex:
                messagebox.showerror(self.t('Greška'), self.t('Ne mogu spremiti alati.json:\n{error}', error=ex))

        def edit_selected():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning(self.t('Nije odabrano'), self.t('Odaberite alat za uređivanje.'))
                return

            item = sel[0]
            tnum, old_name = tree.item(item, 'values')

            new_name = simpledialog.askstring(
                self.t('Uredi alat'),
                self.t('{tool} – novi naziv alata:', tool=tnum),
                initialvalue=old_name
            )
            if new_name is None:
                return

            new_name = new_name.strip()
            if not new_name:
                messagebox.showwarning(self.t('Neispravno'), self.t('Naziv ne smije biti prazan.'))
                return

            tools_map[tnum] = new_name

            for i, (t, _) in enumerate(all_rows):
                if t == tnum:
                    all_rows[i] = (t, new_name)
                    break

            refresh_list()
            save_tools_json()

        tree.bind('<Double-1>', lambda e: edit_selected())

        try:
            search_var.trace_add("write", refresh_list)
        except Exception:
            search_var.trace("w", lambda *args: refresh_list())

        refresh_list()
        ent.focus_set()
        popup.bind('<Escape>', lambda e: popup.destroy())
