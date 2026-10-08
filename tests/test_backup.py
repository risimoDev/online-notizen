import os
import sys
import sqlite3
import zipfile
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["BOT_TOKEN"] = "123456789:TEST_BOT_TOKEN"
os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-testkey"
os.environ["TIMEZONE"] = "Asia/Yekaterinburg"

temp_db = Path(tempfile.gettempdir()) / "test_backup_myzapis.db"
if temp_db.exists():
    temp_db.unlink()
with sqlite3.connect(str(temp_db)) as conn:
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO notes (title) VALUES ('Тестовая заметка')")
conn.close()

os.environ["DB_PATH"] = str(temp_db)

from app.services.backup_service import create_database_backup


def test_backup_creation():
    print("-> Тестирование резервного копирования...")
    zip_path = create_database_backup()
    assert zip_path.exists()
    assert zip_path.stat().st_size > 0

    with zipfile.ZipFile(zip_path, "r") as z:
        files = z.namelist()
        assert temp_db.name in files
        content = z.read(temp_db.name)
        assert content.startswith(b"SQLite format 3")

    # Снимок должен быть рабочей базой с теми же данными
    extracted = Path(tempfile.gettempdir()) / "test_backup_extracted.db"
    extracted.write_bytes(content)
    with sqlite3.connect(str(extracted)) as conn:
        assert conn.execute("SELECT title FROM notes").fetchone()[0] == "Тестовая заметка"
    conn.close()
    extracted.unlink()

    print(f"   [OK] Резервный ZIP-архив успешно создан и проверен ({zip_path.name}).")
    
    # Очистка
    if zip_path.exists():
        zip_path.unlink()
    if temp_db.exists():
        temp_db.unlink()


if __name__ == "__main__":
    test_backup_creation()
    print("\n[SUCCESS] ALL BACKUP TESTS PASSED!")
