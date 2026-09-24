import os
import sys
import zipfile
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def create_release_package():
    base_dir = Path(r"d:\NENGHIA0980\ReviewMatThon")
    output_zip = Path(r"d:\NENGHIA0980\ReviewMatThon_Setup.zip")
    
    print("=" * 70)
    print("DANG DONG GOI BAN CAI DAT: ReviewMatThon_Setup.zip")
    print("=" * 70)
    
    # Danh sach cac thu muc/file can bo qua
    exclude_dirs = {
        "venv", 
        "node_modules", 
        "__pycache__", 
        ".pytest_cache", 
        "scratch", 
        "temp", 
        ".git", 
        ".gemini",
        ".vscode",
        ".idea"
    }
    
    exclude_extensions = {".pyc", ".pyo", ".pyd"}
    
    if output_zip.exists():
        try:
            output_zip.unlink()
        except Exception as e:
            print(f"[!] Khong the xoa zip cu: {e}")

    total_files = 0
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
        for root, dirs, files in os.walk(base_dir):
            root_path = Path(root)
            rel_path = root_path.relative_to(base_dir)
            
            parts = rel_path.parts
            if any(part in exclude_dirs for part in parts):
                continue
                
            for file in files:
                if file.endswith(tuple(exclude_extensions)) or file == "ReviewMatThon_Setup.zip":
                    continue
                    
                file_path = root_path / file
                archive_name = f"ReviewMatThon/{rel_path / file}".replace("\\", "/")
                zipf.write(file_path, archive_name)
                total_files += 1

        # Dam bao cac thu muc input, output, tools rong duoc tao san trong zip
        extra_empty_folders = [
            "ReviewMatThon/tools/",
            "ReviewMatThon/tools/ffmpeg/",
            "ReviewMatThon/tools/ffmpeg/bin/",
            "ReviewMatThon/input/videos/",
            "ReviewMatThon/input/audio_raw/",
            "ReviewMatThon/output/transcripts/",
            "ReviewMatThon/output/translations/",
            "ReviewMatThon/output/voiceover/",
            "ReviewMatThon/output/final_videos/",
            "ReviewMatThon/output/01_raw_cleaned/",
            "ReviewMatThon/output/02_entities/",
            "ReviewMatThon/output/03_dich_ai_llm/",
            "ReviewMatThon/output/04_post_processed/",
        ]
        for folder in extra_empty_folders:
            try:
                zipf.writestr(folder, "")
            except Exception:
                pass

    size_mb = output_zip.stat().st_size / (1024 * 1024)
    print("\n[OK] DA DONG GOI THANH CONG!")
    print(f"-> Vi tri file nen: {output_zip}")
    print(f"-> Tong so tep tin: {total_files}")
    print(f"-> Dung luong file: {size_mb:.2f} MB")
    print("=" * 70)

if __name__ == "__main__":
    create_release_package()
