import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.audio_upload import AudioUploadError, store_audio_asset, validate_audio_upload
from app.config import get_settings
from app.database import Base
from app.models import AudioAsset, AudioAssetStatus


class AudioUploadTests(unittest.TestCase):
    def setUp(self):
        self.database_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.database_file.close()
        self.engine = create_engine(f"sqlite:///{self.database_file.name}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self._tmpdir = tempfile.TemporaryDirectory()
        self._env = patch.dict("os.environ", {"AUDIO_STORAGE_DIR": self._tmpdir.name}, clear=False)
        self._env.start()
        get_settings.cache_clear()

    def tearDown(self):
        self._env.stop()
        get_settings.cache_clear()
        self._tmpdir.cleanup()
        Path(self.database_file.name).unlink(missing_ok=True)

    def test_reupload_after_delete_restores_asset(self):
        db = self.SessionLocal()
        try:
            first, created = store_audio_asset(
                db, get_settings(), filename="intro.mp3", content_type="audio/mpeg", raw=b"same-audio-bytes",
            )
            self.assertTrue(created)
            first.status = AudioAssetStatus.deleted
            db.commit()

            restored, created_again = store_audio_asset(
                db, get_settings(), filename="intro-v2.mp3", content_type="audio/mpeg", raw=b"same-audio-bytes",
            )
            self.assertTrue(created_again)
            self.assertEqual(restored.id, first.id)
            self.assertEqual(restored.status, AudioAssetStatus.ready)
            self.assertEqual(restored.display_name, "intro-v2.mp3")
            self.assertTrue(Path(get_settings().audio_storage_dir, restored.storage_key).is_file())
        finally:
            db.close()

    def test_duplicate_ready_asset_is_reused(self):
        db = self.SessionLocal()
        try:
            first, _ = store_audio_asset(
                db, get_settings(), filename="one.mp3", content_type="audio/mpeg", raw=b"audio",
            )
            second, created = store_audio_asset(
                db, get_settings(), filename="two.mp3", content_type="audio/mpeg", raw=b"audio",
            )
            self.assertFalse(created)
            self.assertEqual(second.id, first.id)
        finally:
            db.close()

    def test_validation_messages(self):
        with self.assertRaisesRegex(AudioUploadError, "MP3 or WAV"):
            validate_audio_upload("notes.txt", "text/plain")


if __name__ == "__main__":
    unittest.main()
