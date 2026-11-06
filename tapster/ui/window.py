"""Desenho da janela. Não decide nada: só mostra o que o TapsterApp pede."""

import tkinter as tk
from tkinter import font as tkfont

from tapster.settings import BUTTONS, SPEED_PRESETS
from tapster.ui import theme
from tapster.ui.state import AppState


class TapsterWindow:
    def __init__(self, root, on_action, on_close):
        self.root = root
        self._state = AppState.IDLE

        root.title("Tapster")
        root.resizable(False, False)
        root.configure(bg=theme.BG)
        # Mantém a janela visível mesmo quando um clique ativa outro aplicativo.
        root.attributes("-topmost", True)
        root.protocol("WM_DELETE_WINDOW", on_close)

        families = set(tkfont.families(root))
        self.family = next((f for f in theme.FONT_CANDIDATES if f in families), theme.FALLBACK_FONT)
        # Fontes crescem em telas HiDPI, mas medidas em pixels não. Esta escala as acompanha.
        self.scale = max(1.0, tkfont.Font(root=root, font=(self.family, 11)).metrics("linespace") / 30)

        self.interval_var = tk.StringVar(value="100")
        self.button_var = tk.StringVar(value="Esquerdo")
        self.limit_enabled_var = tk.BooleanVar(value=False)
        self.limit_var = tk.StringVar(value="100")
        self.delay_var = tk.StringVar(value="3")

        main = tk.Frame(root, bg=theme.BG, padx=self._px(20), pady=self._px(14))
        main.grid()
        main.columnconfigure(0, weight=1)

        # A ordem acompanha o uso: ajustar, iniciar e acompanhar o resultado.
        self._build_header(main).grid(row=0, column=0, sticky="ew", pady=(0, self._px(10)))
        self._build_controls(main).grid(row=1, column=0, sticky="ew", pady=(0, self._px(10)))

        self.action_button = self._flat_button(
            main, font=(self.family, 12, "bold"), pady=self._px(8), command=on_action
        )
        self.action_button.grid(row=2, column=0, sticky="ew", pady=(0, self._px(10)))

        self._build_counter(main).grid(row=3, column=0, sticky="ew")

        self.interval_var.trace_add("write", lambda *_: self._sync_inputs())
        self.limit_enabled_var.trace_add("write", lambda *_: self._sync_inputs())
        self.button_var.trace_add("write", lambda *_: self._sync_inputs())
        self.show_state(AppState.IDLE)

        # A largura vem do formulário; só então os textos longos quebram nela.
        main.update_idletasks()
        text_width = main.winfo_reqwidth() - 2 * self._px(20)
        self.status_label.configure(wraplength=text_width - 2 * self._px(14))

    def form_values(self):
        """Devolve os campos na ordem esperada por parse_settings."""
        return (
            self.interval_var.get().strip(),
            self.limit_enabled_var.get(),
            self.limit_var.get().strip(),
            self.delay_var.get().strip(),
        )

    def selected_button_code(self):
        return BUTTONS[self.button_var.get()]

    def bind_pointer(self, on_enter, on_leave, on_press):
        self.root.bind("<Enter>", on_enter)
        self.root.bind("<Leave>", on_leave)
        self.root.bind_all("<ButtonPress>", on_press, add=True)

    def bring_to_front(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def set_counter(self, value):
        self.counter_label.configure(text=str(value))

    def set_status(self, text, error=False):
        self.status_label.configure(text=text, fg=theme.ERROR if error else theme.TEXT)
        self._fit_window()

    def show_state(self, state):
        self._state = state
        look = theme.STATE_LOOKS[state]
        self.state_pill.configure(text=look.pill_text, bg=look.pill_bg, fg=look.pill_fg)
        self.hero_strip.configure(bg=look.strip)
        self.counter_label.configure(fg=look.counter)
        self.counter_caption.configure(text=look.caption)
        self.action_button.configure(
            text=f"{look.action_text}  ·  F8", bg=look.action_bg, fg=theme.ON_ACCENT,
            activebackground=look.action_hover, activeforeground=theme.ON_ACCENT,
        )
        self._sync_inputs()

    def _build_header(self, parent):
        header = tk.Frame(parent, bg=theme.BG)
        header.columnconfigure(0, weight=1)
        tk.Label(header, text="TAPSTER", bg=theme.BG, fg=theme.TEXT,
                 font=(self.family, 14, "bold")).grid(row=0, column=0, sticky="w")
        self.state_pill = tk.Label(header, font=(self.family, 9, "bold"),
                                   padx=self._px(10), pady=self._px(3))
        self.state_pill.grid(row=0, column=1, sticky="e")
        return header

    def _build_counter(self, parent):
        panel = tk.Frame(parent, bg=theme.PANEL)
        panel.columnconfigure(0, weight=1)
        self.hero_strip = tk.Frame(panel, height=self._px(5))
        self.hero_strip.grid(row=0, column=0, sticky="ew")

        reading = tk.Frame(panel, bg=theme.PANEL)
        reading.grid(row=1, column=0, pady=(self._px(4), 0))
        self.counter_label = tk.Label(reading, text="0", bg=theme.PANEL, font=(self.family, 28, "bold"))
        self.counter_label.grid(row=0, column=0, sticky="s")
        self.counter_caption = tk.Label(reading, text="CLIQUES", bg=theme.PANEL, fg=theme.MUTED,
                                        font=(self.family, 9, "bold"))
        self.counter_caption.grid(row=0, column=1, sticky="s", padx=(self._px(8), 0), pady=(0, self._px(10)))

        # Duas linhas reservadas evitam que o layout pule a cada mensagem.
        self.status_label = tk.Label(panel, bg=theme.PANEL, font=(self.family, 9), justify="center", height=2)
        self.status_label.grid(row=2, column=0, padx=self._px(14), pady=(0, self._px(8)))
        return panel

    def _build_controls(self, parent):
        # Uma única grade mantém rótulos e controles alinhados entre as linhas.
        card = tk.Frame(parent, bg=theme.PANEL, padx=self._px(14), pady=self._px(10))
        for column in (1, 2, 3):
            card.columnconfigure(column, weight=1, uniform="control")

        self._row_label(card, 0, "Velocidade")
        self.speed_chips = {}
        for column, (name, interval_ms) in enumerate(SPEED_PRESETS, start=1):
            chip = self._chip(card, name, lambda value=interval_ms: self.interval_var.set(str(value)))
            self._place_control(chip, 0, column)
            self.speed_chips[interval_ms] = chip

        self._row_label(card, 1, "Intervalo")
        self.interval_entry = self._entry(card, self.interval_var)
        self._place_control(self.interval_entry, 1, 1, columnspan=2)
        self._unit_label(card, 1, "ms")

        self._row_label(card, 2, "Botão")
        self.button_chips = {}
        for column, name in enumerate(BUTTONS, start=1):
            chip = self._chip(card, name, lambda value=name: self.button_var.set(value))
            self._place_control(chip, 2, column)
            self.button_chips[name] = chip

        self._row_label(card, 3, "Atraso")
        self.delay_entry = self._entry(card, self.delay_var)
        self._place_control(self.delay_entry, 3, 1, columnspan=2)
        self._unit_label(card, 3, "segundos")

        self._row_label(card, 4, "Limite")
        self.limit_chip = self._chip(card, "Parar após",
                                     lambda: self.limit_enabled_var.set(not self.limit_enabled_var.get()))
        self._place_control(self.limit_chip, 4, 1)
        self.limit_entry = self._entry(card, self.limit_var)
        self._place_control(self.limit_entry, 4, 2)
        self._unit_label(card, 4, "cliques")
        return card

    def _row_label(self, parent, row, text):
        tk.Label(parent, text=text, bg=theme.PANEL, fg=theme.MUTED, font=(self.family, 10)).grid(
            row=row, column=0, sticky="w", padx=(0, self._px(16)))

    def _unit_label(self, parent, row, text):
        tk.Label(parent, text=text, bg=theme.PANEL, fg=theme.MUTED, font=(self.family, 10)).grid(
            row=row, column=3, sticky="w", padx=self._px(3))

    def _place_control(self, widget, row, column, columnspan=1):
        widget.grid(row=row, column=column, columnspan=columnspan, sticky="nsew",
                    padx=self._px(3), pady=self._px(3))

    def _px(self, value):
        return round(value * self.scale)

    def _flat_button(self, parent, **options):
        return tk.Button(parent, relief="flat", bd=0, highlightthickness=0, cursor="hand2", **options)

    def _chip(self, parent, text, command):
        return self._flat_button(parent, text=text, command=command, font=(self.family, 9, "bold"),
                                 pady=self._px(3), padx=self._px(8))

    def _entry(self, parent, variable):
        return tk.Entry(
            parent, textvariable=variable, width=1, justify="center", relief="flat", bd=0,
            font=(self.family, 10, "bold"), bg=theme.FIELD, fg=theme.TEXT,
            insertbackground=theme.TEXT, disabledbackground=theme.BG, disabledforeground=theme.MUTED,
            highlightthickness=self._px(2), highlightbackground=theme.FIELD, highlightcolor=theme.ACCENT,
        )

    def _sync_inputs(self):
        idle = self._state == AppState.IDLE
        interval = self.interval_var.get().strip()
        for interval_ms, chip in self.speed_chips.items():
            self._paint_chip(chip, interval == str(interval_ms), idle)
        for name, chip in self.button_chips.items():
            self._paint_chip(chip, self.button_var.get() == name, idle)
        limit_enabled = self.limit_enabled_var.get()
        self._paint_chip(self.limit_chip, limit_enabled, idle)
        self.interval_entry.configure(state="normal" if idle else "disabled")
        self.delay_entry.configure(state="normal" if idle else "disabled")
        self.limit_entry.configure(state="normal" if idle and limit_enabled else "disabled")

    def _paint_chip(self, chip, selected, enabled):
        background = theme.ACCENT if selected else theme.FIELD
        foreground = theme.ON_ACCENT if selected else theme.TEXT
        chip.configure(
            bg=background,
            fg=foreground,
            activebackground=theme.ACCENT_HOVER if selected else theme.FIELD_HOVER,
            activeforeground=foreground,
            disabledforeground=theme.ON_ACCENT if selected else theme.MUTED,
            state="normal" if enabled else "disabled",
        )

    def _fit_window(self):
        # Janelas de tamanho fixo não crescem sozinhas se uma mensagem passar de duas linhas.
        lines = self._status_line_count()
        self.status_label.configure(height=max(2, lines))
        self.root.update_idletasks()
        self.root.geometry(f"{self.root.winfo_reqwidth()}x{self.root.winfo_reqheight()}")

    def _status_line_count(self):
        # Sem altura fixa, o rótulo informa de quantas linhas o texto precisa.
        self.status_label.configure(height=0)
        self.root.update_idletasks()
        line_height = tkfont.Font(root=self.root, font=self.status_label.cget("font")).metrics("linespace")
        border = 2 * int(self.status_label.cget("pady")) + 2 * int(self.status_label.cget("bd"))
        return round((self.status_label.winfo_reqheight() - border) / line_height)
