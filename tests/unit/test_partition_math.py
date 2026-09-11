import pytest

from common.chunking import compute_partitions


def test_exact_multiple_of_ten():
    partitions = compute_partitions(1000, num_partitions=10)
    assert len(partitions) == 10
    assert all(end - start + 1 == 100 for start, end in partitions)
    assert partitions[0][0] == 0
    assert partitions[-1][1] == 999


def test_remainder_distributed_to_first_partitions():
    # 1005 bytes / 10 partitions -> base=100, remainder=5
    partitions = compute_partitions(1005, num_partitions=10)
    sizes = [end - start + 1 for start, end in partitions]
    assert sizes == [101, 101, 101, 101, 101, 100, 100, 100, 100, 100]
    assert sum(sizes) == 1005


def test_no_gaps_or_overlaps():
    partitions = compute_partitions(4321, num_partitions=10)
    assert partitions[0][0] == 0
    for (_, prev_end), (next_start, _) in zip(partitions, partitions[1:], strict=False):
        assert next_start == prev_end + 1
    assert partitions[-1][1] == 4320


def test_file_smaller_than_num_partitions():
    # A 3-byte file must not produce 10 partitions.
    partitions = compute_partitions(3, num_partitions=10)
    assert len(partitions) == 3
    assert partitions == [(0, 0), (1, 1), (2, 2)]


def test_single_byte_file():
    partitions = compute_partitions(1, num_partitions=10)
    assert partitions == [(0, 0)]


def test_zero_byte_file_rejected():
    with pytest.raises(ValueError):
        compute_partitions(0)


def test_negative_size_rejected():
    with pytest.raises(ValueError):
        compute_partitions(-5)
