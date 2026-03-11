"""Tests for the SQLite state store."""

from meme_sorter.state import StateStore


def test_new_run(tmp_state):
    run_id = tmp_state.new_run("sort")
    assert run_id
    assert len(run_id) == 12


def test_mark_and_check_processed(tmp_state):
    run_id = tmp_state.new_run("sort")
    assert not tmp_state.is_processed("/fake/path.jpg")
    tmp_state.mark_processed("/fake/path.jpg", "Shitpost", True, "llava", run_id)
    assert tmp_state.is_processed("/fake/path.jpg")


def test_record_and_undo_move(tmp_state, tmp_path):
    # Create real files to move
    src = tmp_path / "Shitpost" / "meme.jpg"
    dest_dir = tmp_path / "Gaming"
    src.parent.mkdir(parents=True)
    dest_dir.mkdir(parents=True)
    src.write_bytes(b"fake")

    dest = dest_dir / "meme.jpg"
    src.rename(dest)

    run_id = tmp_state.new_run("sort")
    tmp_state.record_move(run_id, str(src), str(dest), "Gaming", True)

    batch = tmp_state.get_undo_batch(run_id)
    assert len(batch) == 1
    assert batch[0]["source_path"] == str(src)
    assert batch[0]["dest_path"] == str(dest)


def test_get_stats(tmp_state):
    run_id = tmp_state.new_run("sort")
    tmp_state.mark_processed("/a.jpg", "Shitpost", True, "llava", run_id)
    tmp_state.mark_processed("/b.jpg", "Shitpost", True, "llava", run_id)
    tmp_state.mark_processed("/c.jpg", "Gaming", True, "llava", run_id)

    stats = tmp_state.get_stats()
    assert stats["Shitpost"] == 2
    assert stats["Gaming"] == 1


def test_run_history(tmp_state):
    tmp_state.new_run("sort")
    tmp_state.new_run("recheck")

    history = tmp_state.get_run_history()
    assert len(history) == 2
    assert history[0]["mode"] == "recheck"  # most recent first
