from common.chunking import make_chunk_id


def test_format():
    assert make_chunk_id("uploads/example.txt", 0, 3) == "uploads/example.txt_w0_c3"


def test_deterministic():
    assert make_chunk_id("file.txt", 2, 5) == make_chunk_id("file.txt", 2, 5)


def test_unique_across_worker_index():
    a = make_chunk_id("file.txt", 0, 0)
    b = make_chunk_id("file.txt", 1, 0)
    assert a != b


def test_unique_across_local_chunk_num():
    a = make_chunk_id("file.txt", 0, 0)
    b = make_chunk_id("file.txt", 0, 1)
    assert a != b


def test_unique_across_file_id():
    a = make_chunk_id("a.txt", 0, 0)
    b = make_chunk_id("b.txt", 0, 0)
    assert a != b
