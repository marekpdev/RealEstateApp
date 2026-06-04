import os
import pytest
from pydantic import BaseModel
from utils.utils import get_env_bool, load_mock_fixture, print_model

class MockModel(BaseModel):
    name: str
    age: int

def test_get_env_bool():
    # Test with explicit values
    os.environ["TEST_TRUE"] = "true"
    os.environ["TEST_1"] = "1"
    os.environ["TEST_YES"] = "yes"
    os.environ["TEST_FALSE"] = "false"
    
    assert get_env_bool("TEST_TRUE") is True
    assert get_env_bool("TEST_1") is True
    assert get_env_bool("TEST_YES") is True
    assert get_env_bool("TEST_FALSE") is False

    # Test defaults
    if "MISSING_VAR" in os.environ:
        del os.environ["MISSING_VAR"]
    
    assert get_env_bool("MISSING_VAR", default=True) is True
    assert get_env_bool("MISSING_VAR", default=False) is False

def test_load_mock_fixture(tmp_path, monkeypatch):
    # Create a dummy fixture file
    fixtures_dir = tmp_path / "tests" / "fixtures"
    fixtures_dir.mkdir(parents=True)
    fixture_file = fixtures_dir / "test_fixture.json"
    fixture_file.write_text('{"name": "Test", "age": 30}', encoding="utf-8")
    
    # Patch Path in load_mock_fixture to point to our tmp_path
    # load_mock_fixture uses Path(__file__).parent.parent
    # We can patch the project_root in the function or just mock the whole Path
    
    with monkeypatch.context() as m:
        m.setattr("utils.utils.Path", lambda *args: tmp_path)
        # Note: This is a bit hacky because Path(__file__) is used inside.
        # Let's try a different approach: mock the read_text and exists
        pass

    # Actually, it's easier to just use the real fixtures if possible, or mock the Path properly.
    # But wait, I can just create the file in the real path if I'm allowed, or mock Path.
    
    from pathlib import Path
    original_exists = Path.exists
    original_read_text = Path.read_text
    
    def mock_exists(self):
        if "test_fixture.json" in str(self):
            return True
        return original_exists(self)
    
    def mock_read_text(self, encoding=None):
        if "test_fixture.json" in str(self):
            return '{"name": "Test", "age": 30}'
        return original_read_text(self, encoding=encoding)
    
    monkeypatch.setattr(Path, "exists", mock_exists)
    monkeypatch.setattr(Path, "read_text", mock_read_text)
    
    result = load_mock_fixture("test_fixture.json", MockModel)
    assert result.name == "Test"
    assert result.age == 30

def test_print_model(capsys):
    model = MockModel(name="Test", age=30)
    print_model(model, title="Test Model")
    captured = capsys.readouterr()
    assert "TEST MODEL" in captured.out
    assert "MockModel" in captured.out
    assert '"name": "Test"' in captured.out
    assert '"age": 30' in captured.out

def test_print_model_invalid():
    # Should not crash
    print_model("not a model")
