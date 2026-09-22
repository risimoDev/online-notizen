import os
import sys
import zipfile
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["BOT_TOKEN"] = "123456789:TEST_BOT_TOKEN"
os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-testkey"
os.environ["TIMEZONE"] = "Asia/Yekaterinburg"

temp_db = Path(tempfile.gettempdir()) / "test_backup_myzapis.db"
with open(temp_db, "w", encoding="utf-8") as f:
    f.write("SQLite format 3 - TEST MOCK DATA")

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
        content = z.read(temp_db.name).decode("utf-8")
        assert "SQLite format 3" in content

    print(f"   [OK] Резервный ZIP-архив успешно создан и проверен ({zip_path.name}).")
    
    # Очистка
    if zip_path.exists():
        zip_path.unlink()
    if temp_db.exists():
        temp_db.unlink()


if __name__ == "__main__":
    test_backup_creation()
    print("\n[SUCCESS] ALL BACKUP TESTS PASSED!")
