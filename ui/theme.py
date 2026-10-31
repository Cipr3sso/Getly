"""
Windows 11 Fluent Dark Theme Tokens & Styles for Getly.
Consistent neutral WinUI 3 palette:
- Main Window: #181818
- Navigation Rail / Panels / Dialogs: #202020
- Tables / Cards / TextFields: #2B2B2B
- Borders: #3D3D3D
- Primary Accent: #0078D4 (Fluent Blue)
- Active Highlights: #60CDFF
"""

import flet as ft
from typing import Any, Optional

# 1. Colors - Windows 11 Fluent Dark Neutral Palette
BG_WINDOW = "#181818"            # Sfondo Finestra Principale
BG_PANEL = "#202020"             # Sfondo Navigation Rail / Pannelli / Dialoghi
SURFACE_CARD = "#2B2B2B"         # Superficie Tabella / Card / Input TextField
BORDER_COLOR = "#3D3D3D"         # Bordo Fluent Scuro
BORDER_SUBTLE = "rgba(255, 255, 255, 0.08)"

# Windows 11 Fluent Blue Accents
ACCENT_BLUE = "#0078D4"          # Accent Primario
ACCENT_LIGHT = "#60CDFF"         # Icone / Indicatori Attivi
ACCENT_BLUE_WINUI = "#0067C0"    # Accent Scuro / Pressed
ACCENT_BLUE_LIGHT = "#60CDFF"

# Status colors
COLOR_SUCCESS = "#10B981"
COLOR_SUCCESS_BG = "rgba(16, 185, 129, 0.15)"
COLOR_SUCCESS_BORDER = "rgba(16, 185, 129, 0.35)"

COLOR_WARNING = "#F59E0B"
COLOR_WARNING_BG = "rgba(245, 158, 11, 0.15)"
COLOR_WARNING_BORDER = "rgba(245, 158, 11, 0.35)"

COLOR_ERROR = "#EF4444"
COLOR_ERROR_BG = "rgba(239, 68, 68, 0.15)"
COLOR_ERROR_BORDER = "rgba(239, 68, 68, 0.35)"

COLOR_INFO = "#38BDF8"
COLOR_INFO_BG = "rgba(56, 189, 248, 0.15)"
COLOR_INFO_BORDER = "rgba(56, 189, 248, 0.35)"

# Typography
TEXT_PRIMARY = "#FFFFFF"         # Testo Principale / Titoli
TEXT_SECONDARY = "#A0A0A0"       # Testo Secondario / Label
TEXT_MUTED = "#757575"           # Testo Smorzato / Hint

# Backward compatibility aliases
BG_BASE = BG_WINDOW
BG_BASE_LIGHT = BG_WINDOW
SURFACE_CARD_DARK = SURFACE_CARD
SURFACE_CARD_LIGHT = SURFACE_CARD
BORDER_DARK = BORDER_COLOR
BORDER_LIGHT = BORDER_COLOR
SURFACE_GLASS = BG_PANEL
SURFACE_GLASS_SOLID = BG_PANEL
SURFACE_GLASS_LIGHT = BG_PANEL
SURFACE_HOVER = "rgba(255, 255, 255, 0.06)"

ACCENT_MAGENTA = ACCENT_BLUE
ACCENT_MAGENTA_LIGHT = ACCENT_LIGHT
ACCENT_PURPLE = ACCENT_BLUE_WINUI
ACCENT_CYAN = ACCENT_LIGHT

BORDER_LUMINESCENT = BORDER_COLOR
BORDER_GLOW_MAGENTA = "rgba(0, 120, 212, 0.35)"
BORDER_GLOW_PURPLE = "rgba(0, 103, 192, 0.35)"

TEXT_TITLE = TEXT_PRIMARY
TEXT_SUBTITLE = TEXT_SECONDARY

# 2. Shadows
GLOW_SHADOW_MAGENTA = [
    ft.BoxShadow(
        spread_radius=0,
        blur_radius=10,
        color="rgba(0, 120, 212, 0.25)",
        offset=ft.Offset(0, 2),
    )
]

GLOW_SHADOW_PURPLE = [
    ft.BoxShadow(
        spread_radius=0,
        blur_radius=10,
        color="rgba(0, 103, 192, 0.25)",
        offset=ft.Offset(0, 2),
    )
]

GLASS_CARD_SHADOW = [
    ft.BoxShadow(
        spread_radius=0,
        blur_radius=12,
        color="rgba(0, 0, 0, 0.20)",
        offset=ft.Offset(0, 4),
    )
]

# 3. Gradients
VELVET_PROGRESS_GRADIENT = ft.LinearGradient(
    begin=ft.Alignment(-1, 0),
    end=ft.Alignment(1, 0),
    colors=["#0067C0", "#0078D4"],
)

COMPLETED_PROGRESS_GRADIENT = ft.LinearGradient(
    begin=ft.Alignment(-1, 0),
    end=ft.Alignment(1, 0),
    colors=["#059669", "#34D399"],
)

MAGENTA_BTN_GRADIENT = ft.LinearGradient(
    begin=ft.Alignment(-1, -1),
    end=ft.Alignment(1, 1),
    colors=["#0067C0", "#0078D4"],
)

PURPLE_PILL_GRADIENT = ft.LinearGradient(
    begin=ft.Alignment(-1, 0),
    end=ft.Alignment(1, 0),
    colors=["rgba(0, 120, 212, 0.15)", "rgba(96, 205, 255, 0.15)"],
)


def create_ambient_glow_layer() -> list[ft.Control]:
    """
    Renders an elegant Windows 11 WinUI 3 neutral background layer.
    """
    return [
        ft.Container(
            expand=True,
            gradient=ft.LinearGradient(
                begin=ft.Alignment(-1, -1),
                end=ft.Alignment(1, 1),
                colors=["#202020", "#1C1C1C", "#181818"],
                stops=[0.0, 0.5, 1.0],
            ),
        )
    ]


def build_gradient_progress_bar(progress_ratio: float, status_val: str) -> ft.Container:
    """
    Renders a slim Fluent progress bar with gradient fill between #0078D4 and #60CDFF
    for active downloads, or themed status colors.
    """
    progress = max(0.0, min(1.0, progress_ratio))
    fill_units = int(progress * 1000)
    empty_units = 1000 - fill_units

    if status_val == "COMPLETED":
        return ft.Container(
            height=3,
            border_radius=2,
            bgcolor="#10B981",
        )
    elif status_val in ("FAILED", "CANCELLED"):
        fill_gradient = ft.LinearGradient(colors=["#DC2626", "#EF4444"])
        glow_shadow = [ft.BoxShadow(blur_radius=6, color="rgba(239, 68, 68, 0.3)")]
        bar_height = 3
        track_color = "rgba(255, 255, 255, 0.10)"
    elif status_val == "PAUSED" or fill_units == 0:
        fill_gradient = ft.LinearGradient(colors=["#D97706", "#F59E0B"])
        glow_shadow = [ft.BoxShadow(blur_radius=6, color="rgba(245, 158, 11, 0.3)")]
        bar_height = 3
        track_color = "rgba(255, 255, 255, 0.10)"
    else:
        # DOWNLOADING: Windows 11 Fluent Blue (#0078D4 to #60CDFF)
        fill_gradient = ft.LinearGradient(colors=["#0078D4", "#60CDFF"])
        glow_shadow = [ft.BoxShadow(blur_radius=8, color="rgba(0, 120, 212, 0.35)")]
        bar_height = 4
        track_color = "#3D3D3D"

    controls = []
    if fill_units > 0:
        controls.append(
            ft.Container(
                expand=fill_units,
                height=bar_height,
                border_radius=10,
                gradient=fill_gradient,
                shadow=glow_shadow,
            )
        )
    if empty_units > 0:
        controls.append(
            ft.Container(
                expand=empty_units,
                height=bar_height,
                bgcolor="transparent",
            )
        )

    return ft.Container(
        height=bar_height,
        border_radius=10,
        bgcolor=track_color,
        border=ft.Border.all(1, "rgba(255, 255, 255, 0.05)"),
        content=ft.Row(spacing=0, controls=controls),
    )


def safe_update(control: ft.Control) -> None:
    """Updates a control surgically without raising RuntimeError or updating the entire Page."""
    try:
        if control is not None and hasattr(control, "update"):
            control.update()
    except Exception:
        pass


def safe_page_update(control_or_page: Any) -> None:
    """Safely updates the page containing the control if mounted, suppressing RuntimeError."""
    try:
        if control_or_page is None:
            return
        if hasattr(control_or_page, "update") and type(control_or_page).__name__ == "Page":
            control_or_page.update()
            return
        page = None
        try:
            page = getattr(control_or_page, "page", None)
        except (RuntimeError, Exception):
            page = None
        if page is not None and hasattr(page, "update"):
            page.update()
    except Exception:
        pass


def build_fluent_text_field(
    label: str = "",
    hint_text: str = "",
    value: str = "",
    width: float | None = None,
    expand: bool = False,
    on_change: object = None,
    **kwargs,
) -> ft.TextField:
    """
    Builds a standard Windows 11 Fluent Dark TextField:
    #2B2B2B background, #FFFFFF text, #A0A0A0 label, #3D3D3D border.
    Never white on dark!
    """
    params = {
        "label": label if label else None,
        "hint_text": hint_text if hint_text else None,
        "value": value,
        "bgcolor": SURFACE_CARD,
        "color": TEXT_PRIMARY,
        "border_color": BORDER_COLOR,
        "focused_border_color": ACCENT_BLUE,
        "cursor_color": ACCENT_LIGHT,
        "label_style": ft.TextStyle(color=TEXT_SECONDARY, size=12),
        "hint_style": ft.TextStyle(color=TEXT_MUTED, size=12),
        "text_style": ft.TextStyle(color=TEXT_PRIMARY, size=12),
        "border_radius": 6,
        "dense": True,
        "width": width,
        "expand": expand,
        "on_change": on_change,
    }
    params.update(kwargs)
    return ft.TextField(**params)
