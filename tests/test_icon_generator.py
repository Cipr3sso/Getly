from pathlib import Path
from PIL import Image
import pytest

from ui.icon_generator import (
    create_rocket_icon,
    get_rocket_icon_image,
    ensure_app_icons,
    set_windows_app_user_model_id,
    apply_windows_taskbar_icon,
)


def test_create_rocket_icon():
    img = create_rocket_icon(size=256)
    assert isinstance(img, Image.Image)
    assert img.size == (256, 256)
    assert img.mode == "RGBA"

    # Verify colors exist in the squircle gradient (purple top-left, magenta bottom-right)
    # Sample near top-left inside squircle
    tl_pixel = img.getpixel((80, 80))
    assert tl_pixel[3] > 0  # Not transparent
    assert tl_pixel[0] > 100  # Red channel
    assert tl_pixel[2] > 200  # Blue channel high (purple/ametista)

    # Sample near center where white rocket/porthole outline is drawn
    center_pixel = img.getpixel((128, 128))
    assert center_pixel[3] > 0

    # Test small size
    small_img = get_rocket_icon_image(64)
    assert small_img.size == (64, 64)


def test_ensure_app_icons_generation(tmp_path: Path):
    paths = ensure_app_icons(project_root=tmp_path, force=True)

    ico = paths["ico"]
    png = paths["png"]
    ext_16 = paths["ext_16"]
    ext_32 = paths["ext_32"]
    ext_48 = paths["ext_48"]
    ext_128 = paths["ext_128"]

    assert ico.exists()
    assert png.exists()
    assert ext_16.exists()
    assert ext_32.exists()
    assert ext_48.exists()
    assert ext_128.exists()

    # Verify PNG validity
    with Image.open(png) as im:
        assert im.format == "PNG"
        assert im.size == (256, 256)

    # Verify ICO validity
    with Image.open(ico) as im_ico:
        assert im_ico.format == "ICO"
        assert im_ico.size == (256, 256)

    # Calling again without force should return existing files quickly
    cached_paths = ensure_app_icons(project_root=tmp_path, force=False)
    assert cached_paths["ico"] == ico


def test_windows_branding_safe_execution():
    # Should run without throwing errors regardless of platform
    res = set_windows_app_user_model_id("resilient.heisenberg.downloader.test")
    assert isinstance(res, bool)

    # Invalid icon path should return False safely
    res_apply = apply_windows_taskbar_icon("non_existent_file.ico")
    assert res_apply is False
