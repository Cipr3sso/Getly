"""
Rocket Icon Generator & Windows Taskbar Brand Manager for Resilient Heisenberg.
Generates multi-resolution .ico and crisp .png icons with diagonal purple-magenta
gradient squircle and sharp 45-degree rocket silhouette, with 4x supersampling.
"""

import math
import os
import sys
import logging
from collections import deque
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

import numpy as np
from PIL import Image, ImageDraw

from core.system.paths import is_frozen, get_bundle_dir, get_app_data_dir

logger = logging.getLogger(__name__)


def process_custom_logo(custom_logo_path: Path, target_size: int = 256) -> Image.Image:
    """
    Loads custom_logo.png, removes the outer background outside the squircle
    by making it transparent via flood-fill from the 4 corners, and resizes to target_size.
    """
    img = Image.open(custom_logo_path).convert("RGBA")
    w, h = img.size
    arr = np.array(img)

    visited = np.zeros((h, w), dtype=bool)
    corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    q = deque(corners)
    for cx, cy in corners:
        visited[cy, cx] = True

    while q:
        x, y = q.popleft()
        arr[y, x, 3] = 0  # Set alpha to 0 (transparent)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and not visited[ny, nx]:
                r, g, b = arr[ny, nx, 0], arr[ny, nx, 1], arr[ny, nx, 2]
                if r >= 235 and g >= 235 and b >= 235:
                    visited[ny, nx] = True
                    q.append((nx, ny))

    processed_img = Image.fromarray(arr, mode="RGBA")
    if target_size != w:
        return processed_img.resize((target_size, target_size), resample=Image.Resampling.LANCZOS)
    return processed_img


def find_custom_logo_path() -> Optional[Path]:
    """Finds custom_logo.png in project assets or bundle directories."""
    candidates = [
        Path(__file__).resolve().parent.parent / "assets" / "custom_logo.png",
        get_bundle_dir() / "assets" / "custom_logo.png",
        get_app_data_dir() / "assets" / "custom_logo.png",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def create_rocket_icon(size: int = 256) -> Image.Image:
    """
    Renders the Getly logo icon at `size` x `size`.
    If custom_logo.png exists, uses it with corner transparency flood-fill.
    Otherwise renders procedural 4x supersampled vector rocket icon.
    """
    custom_logo = find_custom_logo_path()
    if custom_logo and custom_logo.exists():
        try:
            return process_custom_logo(custom_logo, target_size=size)
        except Exception as exc:
            logger.warning("Could not process custom_logo.png, falling back to procedural icon: %s", exc)

    S = 1024  # Base supersampling dimension

    # 1. Diagonal Gradient from #7C3AED (Top-Left) to #FF2E93 (Bottom-Right)
    X, Y = np.meshgrid(np.linspace(0.0, 1.0, S), np.linspace(0.0, 1.0, S))
    T = (X + Y) / 2.0
    c1 = np.array([124, 58, 237], dtype=np.float32)   # #7C3AED
    c2 = np.array([255, 46, 147], dtype=np.float32)   # #FF2E93
    grad_rgb = (1.0 - T[:, :, None]) * c1 + T[:, :, None] * c2
    grad_img = Image.fromarray(grad_rgb.astype(np.uint8), mode="RGB").convert("RGBA")

    # 2. Squircle Mask (rounded rectangle)
    margin = 52
    radius = 210
    mask = Image.new("L", (S, S), 0)
    draw_mask = ImageDraw.Draw(mask)
    draw_mask.rounded_rectangle([margin, margin, S - margin, S - margin], radius=radius, fill=255)

    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    base.paste(grad_img, (0, 0), mask=mask)

    # 3. Vector Silhouette Layer for Rocket
    rocket_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(rocket_layer)

    # 45-degree rotation pointing to top-right
    def rot(x: float, y: float) -> Tuple[float, float]:
        rad = math.radians(45.0)
        rx = x * math.cos(rad) - y * math.sin(rad)
        ry = x * math.sin(rad) + y * math.cos(rad)
        return (512.0 + rx, 512.0 + ry)

    # A. Side Fins
    r_fin = [
        rot(94, 40), rot(145, 90), rot(200, 150), rot(215, 205),
        rot(170, 200), rot(120, 185), rot(92, 160)
    ]
    l_fin = [
        rot(-94, 40), rot(-145, 90), rot(-200, 150), rot(-215, 205),
        rot(-170, 200), rot(-120, 185), rot(-92, 160)
    ]
    draw.polygon(r_fin, fill=(255, 255, 255, 255))
    draw.polygon(l_fin, fill=(255, 255, 255, 255))

    # B. Propulsion Flame (Outer)
    flame = [
        rot(-42, 190), rot(-65, 235), rot(-38, 250), rot(-55, 285),
        rot(0, 360),
        rot(55, 285), rot(38, 250), rot(65, 235), rot(42, 190)
    ]
    draw.polygon(flame, fill=(255, 255, 255, 255))

    # C. Engine Nozzle
    nozzle = [rot(-44, 155), rot(44, 155), rot(50, 190), rot(-50, 190)]
    draw.polygon(nozzle, fill=(255, 255, 255, 255))

    # D. Ogive Fuselage Body
    fuselage = [
        rot(0, -250), # Nose tip
        rot(26, -200), rot(58, -130), rot(82, -60), rot(96, 20), rot(98, 90), rot(92, 160),
        rot(45, 155), rot(0, 150), rot(-45, 155), # Bottom arch
        rot(-92, 160), rot(-98, 90), rot(-96, 20), rot(-82, -60), rot(-58, -130), rot(-26, -200)
    ]
    draw.polygon(fuselage, fill=(255, 255, 255, 255))

    # E. Circular Porthole with cutout glass
    px, py = rot(0, -35)
    r_outer = 48
    r_inner = 30
    draw.ellipse([px - r_outer, py - r_outer, px + r_outer, py + r_outer], fill=(255, 255, 255, 255))
    draw.ellipse([px - r_inner, py - r_inner, px + r_inner, py + r_inner], fill=(0, 0, 0, 0))

    # F. Inner Flame cutout
    inner_flame = [rot(-24, 195), rot(-32, 235), rot(0, 290), rot(32, 235), rot(24, 195)]
    draw.polygon(inner_flame, fill=(0, 0, 0, 0))

    # 4. Composite rocket onto gradient squircle
    final_1024 = Image.alpha_composite(base, rocket_layer)

    if size != 1024:
        return final_1024.resize((size, size), resample=Image.Resampling.LANCZOS)
    return final_1024


def get_rocket_icon_image(size: int = 64) -> Image.Image:
    """Returns a Pillow Image of the rocket logo at the requested size."""
    return create_rocket_icon(size=size)


def ensure_app_icons(project_root: Optional[Path] = None, force: bool = False) -> Dict[str, Path]:
    """
    Generates and saves app_icon.ico, app_icon.png, and extension icons if missing or forced.
    Returns dictionary of generated file paths.
    Safe for frozen environments (read-only bundle vs writable app data).
    """
    if is_frozen() and not project_root:
        bundle_assets = get_bundle_dir() / "assets"
        bundle_ico = bundle_assets / "app_icon.ico"
        bundle_png = bundle_assets / "app_icon.png"
        if bundle_ico.exists() and not force:
            return {"ico": bundle_ico, "png": bundle_png}
        # Fallback to writable app data if missing from bundle
        root = get_app_data_dir()
    else:
        root = project_root or Path(__file__).resolve().parent.parent

    assets_dir = root / "assets"
    ext_icons_dir = root / "extension" / "icons"

    try:
        assets_dir.mkdir(parents=True, exist_ok=True)
        ext_icons_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Read-only fallback
        root = get_app_data_dir()
        assets_dir = root / "assets"
        ext_icons_dir = root / "extension" / "icons"
        assets_dir.mkdir(parents=True, exist_ok=True)
        ext_icons_dir.mkdir(parents=True, exist_ok=True)

    ico_path = assets_dir / "app_icon.ico"
    png_path = assets_dir / "app_icon.png"

    ext_icon_sizes = [16, 32, 48, 128]
    ext_paths = {size: ext_icons_dir / f"icon{size}.png" for size in ext_icon_sizes}

    # Check if files already exist
    all_exist = ico_path.exists() and png_path.exists() and all(p.exists() for p in ext_paths.values())
    if all_exist and not force:
        return {"ico": ico_path, "png": png_path, **{f"ext_{s}": p for s, p in ext_paths.items()}}

    logger.info("Generating high-definition rocket brand icons...")

    # Render base 256x256 image
    img_256 = create_rocket_icon(256)
    img_256.save(png_path, format="PNG")

    # Multi-resolution ICO: 16, 32, 48, 64, 128, 256
    ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img_256.save(ico_path, format="ICO", sizes=ico_sizes)

    # Extension icons
    for size, path in ext_paths.items():
        resized = img_256.resize((size, size), resample=Image.Resampling.LANCZOS)
        resized.save(path, format="PNG")

    logger.info("Successfully generated app icons in %s and %s", assets_dir, ext_icons_dir)
    return {"ico": ico_path, "png": png_path, **{f"ext_{s}": p for s, p in ext_paths.items()}}


def register_windows_app_user_model_id(app_id: str = "Getly", ico_path: Optional[str] = None) -> bool:
    """Registers AppUserModelId in HKCU\\Software\\Classes\\AppUserModelId\\<app_id> with DisplayName and IconUri."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key_path = fr"Software\Classes\AppUserModelId\{app_id}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "Getly")
            if not ico_path:
                found_ico = find_custom_logo_path()
                if found_ico:
                    potential_ico = found_ico.parent / "app_icon.ico"
                    if potential_ico.exists():
                        ico_path = str(potential_ico)
            if ico_path and Path(ico_path).exists():
                winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(Path(ico_path).resolve()))
        return True
    except Exception as exc:
        logger.debug("Could not register AppUserModelId in HKCU registry: %s", exc)
        return False


def set_windows_app_user_model_id(app_id: str = "Getly", ico_path: Optional[str] = None) -> bool:
    """Sets the Windows AppUserModelID so the process has its own distinct taskbar group, toast title and icon."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        register_windows_app_user_model_id(app_id, ico_path)
        return True
    except Exception as exc:
        logger.debug("Failed to set AppUserModelID: %s", exc)
        return False


def apply_windows_taskbar_icon(ico_path: str, window_title: str = "Resilient Download Manager | Net-Merge & CNL2") -> bool:
    """
    Uses Win32 APIs (FindWindowW, LoadImageW, SendMessageW) to set the taskbar and titlebar icon
    directly on Windows 10/11 even when executed via python.exe.
    """
    if sys.platform != "win32":
        return False

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        # Resolve HWND
        hwnd = user32.FindWindowW(None, window_title)
        if not hwnd:
            hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False

        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1

        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
            ctypes.c_int, ctypes.c_int, wintypes.UINT
        ]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.SendMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        user32.SendMessageW.restype = wintypes.LPARAM

        abs_path = os.path.abspath(ico_path)
        if not os.path.exists(abs_path):
            return False

        h_big = user32.LoadImageW(None, abs_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        h_small = user32.LoadImageW(None, abs_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)

        if h_big:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_big)
        if h_small:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_small)

        logger.debug("Applied Win32 taskbar icons to HWND %s", hwnd)
        return True
    except Exception as exc:
        logger.debug("Failed to apply Win32 taskbar icon: %s", exc)
        return False
