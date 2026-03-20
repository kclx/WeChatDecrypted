from __future__ import annotations

import shutil
from pathlib import Path


def collect_wechat_db_files(wechat_root: Path) -> list[Path]:
    if not wechat_root.exists():
        raise FileNotFoundError(f"WECHAT_ROOT does not exist: {wechat_root}")
    if not wechat_root.is_dir():
        raise NotADirectoryError(f"WECHAT_ROOT is not a directory: {wechat_root}")
    return sorted(path for path in wechat_root.rglob("*.db") if path.is_file())


def copy_db_files_to_output(db_files: list[Path], output_root: Path) -> list[Path]:
    copied_files: list[Path] = []
    output_root.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()
    for db_file in db_files:
        target_name = db_file.name
        if target_name in used_names:
            raise ValueError(f"duplicate target database name: {target_name}")
        used_names.add(target_name)
        target_path = output_root / target_name
        shutil.copy2(db_file, target_path)
        copied_files.append(target_path)
    return copied_files


def decrypt_db_files_in_directory(
    probe,
    input_root: Path,
    output_root: Path,
    *,
    recursive: bool = True,
) -> list[dict[str, object]]:
    input_root = Path(input_root)
    output_root = Path(output_root)

    if not input_root.exists():
        raise FileNotFoundError(f"input directory does not exist: {input_root}")
    if not input_root.is_dir():
        raise NotADirectoryError(f"input path is not a directory: {input_root}")

    output_root.mkdir(parents=True, exist_ok=True)
    db_files = sorted(
        input_root.rglob("*.db") if recursive else input_root.glob("*.db")
    )
    results: list[dict[str, object]] = []

    for db_file in db_files:
        probe_result = probe.decrypt_first_page(db_file)
        output_path = output_root / db_file.name
        derived_key = None
        decrypted = False

        if probe_result["header_ok"]:
            derived_key = probe.decrypt_db(db_file, output_path)
            decrypted = True

        results.append(
            {
                "input_path": str(db_file),
                "output_path": str(output_path),
                "header_ok": bool(probe_result["header_ok"]),
                "salt_matches_capture": bool(probe_result["salt_matches_capture"]),
                "derived_key_hex": None if derived_key is None else derived_key.hex(),
                "decrypted": decrypted,
            }
        )

    return results
