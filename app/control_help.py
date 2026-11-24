"""Hover and focus help for the legacy Tk controls."""

from __future__ import annotations

import tkinter as tk
from typing import TypeVar

Widget = TypeVar("Widget", bound=tk.Misc)


def control_help(widget: Widget, text: str) -> Widget:
    """Add a delayed tooltip and return the widget for normal layout."""
    pending: str | None = None
    popup: tk.Toplevel | None = None

    def hide(_event: tk.Event | None = None) -> None:
        nonlocal pending, popup
        if pending is not None:
            widget.after_cancel(pending)
            pending = None
        if popup is not None:
            if popup.winfo_exists():
                popup.destroy()
            popup = None

    def show() -> None:
        nonlocal pending, popup
        pending = None
        if not widget.winfo_viewable():
            return
        popup = tk.Toplevel(widget)
        popup.withdraw()
        popup.overrideredirect(True)
        tk.Label(
            popup, text=text, justify="left", wraplength=320, padx=8, pady=5,
            background="#142235", foreground="#f4f8ff", borderwidth=1, relief="solid",
        ).pack()
        popup.update_idletasks()
        width, height = popup.winfo_reqwidth(), popup.winfo_reqheight()
        x = min(widget.winfo_rootx(), widget.winfo_screenwidth() - width - 8)
        y = widget.winfo_rooty() + widget.winfo_height() + 5
        if y + height > widget.winfo_screenheight():
            y = widget.winfo_rooty() - height - 5
        popup.geometry(f"+{max(0, x)}+{max(0, y)}")
        popup.deiconify()
        popup.lift()

    def queue(_event: tk.Event) -> None:
        nonlocal pending
        hide()
        pending = widget.after(500, show)

    for event in ("<Enter>", "<FocusIn>"):
        widget.bind(event, queue, add="+")
    for event in ("<Leave>", "<FocusOut>", "<ButtonPress>", "<Escape>", "<Unmap>", "<Destroy>"):
        widget.bind(event, hide, add="+")
    return widget
