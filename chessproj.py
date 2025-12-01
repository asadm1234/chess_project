import threading, queue, time, serial
import tkinter as tk
from pathlib import Path
from PIL import Image, ImageTk
import chess, chess.engine
from chess.engine import Limit
import serial.tools.list_ports

SERIAL_PORT = 'auto'
BAUD_RATE = 115200
STOCKFISH_PATH = r'C:\Users\asadm\Downloads\stockfish\stockfish\stockfish-windows-x86-64-avx2.exe'
PIECES_DIR = Path("assets/pieces/lichess_cburnett")

def autodetect_serial(preferred=None):
    ports = list(serial.tools.list_ports.comports())
    if not ports: return None
    if preferred:
        for p in ports:
            if p.device == preferred: return p.device
    ARDUINO_VIDS = {0x2341, 0x2A03}
    FRIENDLY_VIDS = ARDUINO_VIDS | {0x1B4F, 0x239A, 0x1A86, 0x10C4}
    scored = []
    for p in ports:
        desc = (p.description or "").lower()
        vid = getattr(p, "vid", None)
        s = 0
        if "arduino" in desc or "uno" in desc or "r4" in desc: s += 5
        if vid in ARDUINO_VIDS: s += 5
        if vid in FRIENDLY_VIDS: s += 2
        if "usb" in desc or "cdc" in desc: s += 1
        scored.append((s, p))
    scored.sort(reverse=True, key=lambda x: x[0])
    return scored[0][1].device if scored else None

def move_to_notation(board, move):
    try: return board.san(move)
    except Exception: return move.uci()

def eval_cp_white_pov(board, score):
    pov = score.pov(chess.WHITE)
    if pov.is_mate():
        sign = 1 if pov.mate() and pov.mate() > 0 else -1
        dist = abs(pov.mate())
        return sign * (10000 - min(9900, dist * 100))
    return pov.score(mate_score=10000)

def eval_txt(cp):
    return f"{(cp/100.0):+,.2f}".replace(",", "")

class MoveList:
    def __init__(self, parent):
        wrap = tk.Frame(parent)
        tk.Label(wrap, text="Moves", anchor="w").pack(fill="x")
        self.text = tk.Text(wrap, width=26, height=28, state="disabled")
        self.text.pack(fill="both", expand=True)
        self.moves = []
        self.ply = 0
        self.widget = wrap

    def clear(self):
        self.moves.clear(); self.ply = 0
        self._render()

    def add(self, san):
        self.moves.append(san); self.ply += 1
        self._render()

    def _render(self):
        lines = []
        for i in range(0, len(self.moves), 2):
            n = i // 2 + 1
            w = self.moves[i]
            b = self.moves[i+1] if i+1 < len(self.moves) else ""
            lines.append(f"{n}. {w} {b}".rstrip())
        out = "\n".join(lines)
        self.text.config(state="normal"); self.text.delete("1.0", "end")
        self.text.insert("end", out); self.text.see("end"); self.text.config(state="disabled")

class ChessGUI:
    LIGHT = "#EEEED2"; DARK = "#769656"
    H_FROM = "#F6F669"; H_TO = "#F6A800"

    def __init__(self, title="Chess Bridge — Moves & Telemetry"):
        self.root = tk.Tk(); self.root.title(title)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=0)

        self.canvas = tk.Canvas(self.root, bg="#222222", highlightthickness=0)
        self.canvas.grid(row=0, column=0, padx=8, pady=8, sticky="nsew")

        right = tk.Frame(self.root); right.grid(row=0, column=1, sticky="ns", padx=(0,8), pady=8)
        self.status = tk.Label(right, text="Starting…", anchor="w", justify="left"); self.status.pack(fill="x")
        self.moves_panel = MoveList(right); self.moves_panel.widget.pack(fill="both", expand=True, pady=(8,8))
        self.log = tk.Text(right, width=26, height=8, state="disabled"); self.log.pack(fill="x")

        self.q = queue.Queue(); self._running = True
        self._img_cache = {}; self._base_images = {}; self._load_piece_images()
        self.cell = 80
        self._board_snapshot = chess.Board()
        self._last_snapshot = None

        self._resize_after = None
        self.root.bind("<Configure>", self._debounced_resize)
        self.root.bind("<F11>", self._toggle_zoomed)
        self.root.bind("<Shift-F11>", self._toggle_full)
        self.root.bind("<Escape>", self._exit_full)

        menubar = tk.Menu(self.root)
        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="Toggle Maximize (F11)", command=self._toggle_zoomed)
        view_menu.add_command(label="Toggle True Fullscreen (Shift+F11)", command=self._toggle_full)
        view_menu.add_command(label="Exit Fullscreen (Esc)", command=self._exit_full)
        menubar.add_cascade(label="View", menu=view_menu)
        self.root.config(menu=menubar)

        self._redraw_now()
        self.root.after(30, self._pump)

    _is_zoomed = False
    _is_fullscreen = False
    def _toggle_zoomed(self, event=None):
        self._is_zoomed = not self._is_zoomed
        try: self.root.state('zoomed' if self._is_zoomed else 'normal')
        except tk.TclError: self._toggle_full()
        self._redraw_now()
    def _toggle_full(self, event=None):
        self._is_fullscreen = not self._is_fullscreen
        self.root.attributes("-fullscreen", self._is_fullscreen)
        self._redraw_now()
    def _exit_full(self, event=None):
        if self._is_fullscreen:
            self._is_fullscreen = False
            self.root.attributes("-fullscreen", False)
        self._redraw_now()

    def _on_close(self):
        self._running = False; self.root.quit()
    def mainloop(self): self.root.mainloop()

    def _load_piece_images(self):
        need = {'wK':'wK.png','wQ':'wQ.png','wR':'wR.png','wB':'wB.png','wN':'wN.png','wP':'wP.png',
                'bK':'bK.png','bQ':'bQ.png','bR':'bR.png','bB':'bB.png','bN':'bN.png','bP':'bP.png'}
        for key,fname in need.items():
            path = PIECES_DIR / fname
            if path.exists(): self._base_images[key] = Image.open(path).convert("RGBA")
        if not self._base_images: self.set_status("Piece images missing; falling back to text glyphs.")

    def _get_photo(self, symbol: str):
        is_white = symbol.isupper(); key = ('w' if is_white else 'b') + symbol.upper()
        base = self._base_images.get(key)
        if base is None: return None
        ckey = (key, self.cell)
        if ckey in self._img_cache: return self._img_cache[ckey]
        pad = max(0, int(self.cell * 0.12)); tgt = max(8, self.cell - pad)
        ph = ImageTk.PhotoImage(base.resize((tgt, tgt), Image.LANCZOS))
        self._img_cache[ckey] = ph; return ph

    def set_status(self, text):
        self.status.config(text=text)

    def append_log(self, text):
        self.log.config(state="normal"); self.log.insert("end", text + "\n")
        self.log.see("end"); self.log.config(state="disabled")

    def update_board(self, board: chess.Board, last_move=None):
        self._board_snapshot = board.copy()
        self._last_snapshot = last_move
        self.q.put(("board", None))

    def _pump(self):
        try:
            while True:
                kind, _ = self.q.get_nowait()
                if kind == "board": self._redraw_now()
        except queue.Empty:
            pass
        if self._running: self.root.after(30, self._pump)

    def _debounced_resize(self, event):
        if event.widget not in (self.root, self.canvas): return
        if self._resize_after is not None:
            self.root.after_cancel(self._resize_after)
        self._resize_after = self.root.after(40, self._redraw_now)

    def _layout_metrics(self):
        w = max(64, self.canvas.winfo_width())
        h = max(64, self.canvas.winfo_height())
        cell = max(32, min(w // 8, h // 8))
        board_px = cell * 8
        ox = max(0, (w - board_px) // 2)
        oy = max(0, (h - board_px) // 2)
        return cell, ox, oy

    def _redraw_now(self):
        self.cell, _, _ = self._layout_metrics()
        self._img_cache.clear()
        self._draw_board(self._board_snapshot, self._last_snapshot)

    def _draw_board(self, board: chess.Board, last_move):
        self.canvas.delete("all")
        cell, ox, oy = self._layout_metrics()
        self.cell = cell
        for rank in range(8):
            for file in range(8):
                x0 = ox + file * cell; y0 = oy + (7 - rank) * cell
                x1 = x0 + cell;        y1 = y0 + cell
                base = self.LIGHT if (file + rank) % 2 == 0 else self.DARK
                fill = base
                if last_move:
                    ff, rf = chess.square_file(last_move[0]), chess.square_rank(last_move[0])
                    ft, rt = chess.square_file(last_move[1]), chess.square_rank(last_move[1])
                    if file == ff and rank == rf: fill = self.H_FROM
                    elif file == ft and rank == rt: fill = self.H_TO
                self.canvas.create_rectangle(x0, y0, x1, y1, fill=fill, width=0)
        for sq in chess.SQUARES:
            p = board.piece_at(sq)
            if not p: continue
            f = chess.square_file(sq); r = chess.square_rank(sq)
            cx = ox + f * cell + cell // 2
            cy = oy + (7 - r) * cell + cell // 2
            ph = self._get_photo(p.symbol())
            if ph: self.canvas.create_image(cx, cy, image=ph)
            else:  self.canvas.create_text(cx, cy, text=p.symbol(), font=("Arial", int(cell * 0.6)))
        for f in range(8):
            self.canvas.create_text(ox + f * cell + 8, oy + 8 + 7 * cell, text=chr(ord('a') + f), anchor="sw", fill="#333")
        for r in range(8):
            self.canvas.create_text(ox + 8, oy + (7 - r) * cell + 16, text=str(r + 1), anchor="nw", fill="#333")

class Bridge:
    def __init__(self, gui: ChessGUI):
        self.gui = gui
        self.board = chess.Board()
        self.engine = None; self.ser = None
        self.thread = None; self.running = False

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.running = True; self.thread.start()

    def stop(self): self.running = False

    def _send_eval_text(self):
        info = self.engine.analyse(self.board, Limit(time=0.2))
        cp = eval_cp_white_pov(self.board, info["score"])
        self.ser.write(f"EVALTXT:{eval_txt(cp)}\n".encode())

    def _run(self):
        port = autodetect_serial(None if SERIAL_PORT == 'auto' else SERIAL_PORT)
        if not port:
            self.gui.set_status("✗ No serial port found"); return
        self.gui.set_status(f"Connecting: {port} …")
        try:
            self.ser = serial.Serial(port, BAUD_RATE, timeout=1, write_timeout=1, dsrdtr=False, rtscts=False)
            time.sleep(2.5)
            self.gui.set_status(f"✓ Arduino: {port}")
        except Exception as e:
            self.gui.set_status(f"✗ Serial error on {port}: {e}"); return

        self.gui.set_status("Loading Stockfish…")
        try:
            self.engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
            self.gui.set_status("✓ Stockfish ready")
        except Exception as e:
            self.gui.set_status(f"✗ Stockfish error: {e}")
            try: self.ser.close()
            except: pass
            return

        self.board.reset(); self.gui.update_board(self.board, None)
        self.gui.set_status("Waiting for Arduino moves…")
        self.moves_panel = self.gui.moves_panel

        try:
            while self.running:
                if self.ser.in_waiting > 0:
                    line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    if not line: continue

                    if line.startswith("MOVE:"):
                        uci = line.split(':', 1)[1]
                        try:
                            mv = chess.Move.from_uci(uci)
                            if mv in self.board.legal_moves:
                                san = move_to_notation(self.board, mv)
                                self.board.push(mv)
                                self.gui.update_board(self.board, (mv.from_square, mv.to_square))
                                self.moves_panel.add(san)

                                self.ser.write(b"CHECK:1\n" if self.board.is_check() else b"CHECK:0\n")
                                self._send_eval_text()
                                if self.board.is_game_over(): continue

                                result = self.engine.play(self.board, Limit(time=1.0))
                                emv = result.move
                                esan = move_to_notation(self.board, emv)
                                self.ser.write(f"ENGINE:{emv.uci()}:{esan}\n".encode())

                                self.board.push(emv)
                                self.gui.update_board(self.board, (emv.from_square, emv.to_square))
                                self.moves_panel.add(esan)

                                self.ser.write(b"CHECK:1\n" if self.board.is_check() else b"CHECK:0\n")
                                self._send_eval_text()
                            else:
                                self.ser.write(b"ERR:ILLEGAL\n")
                        except Exception:
                            self.ser.write(b"ERR:PARSE\n")

                    elif line == "NEWGAME":
                        self.board.reset()
                        self.gui.update_board(self.board, None)
                        self.moves_panel.clear()
                        self.ser.write(b"NEWGAME\n"); self.ser.write(b"CHECK:0\n")
                time.sleep(0.02)
        finally:
            try:
                if self.engine: self.engine.quit()
            except Exception: pass
            try:
                if self.ser: self.ser.close()
            except Exception: pass

def main():
    gui = ChessGUI()
    br  = Bridge(gui); br.start()
    try: gui.mainloop()
    finally: br.stop()

if __name__ == "__main__":
    main()
