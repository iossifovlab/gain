# pylint: disable=C0114,C0116
"""The process-wide memo of objects built from a resource or a file.

``config_memo_key`` is the one spelling of "config -> memo key": both
``GenomicResource.get_memo_key()`` and the two ``build_*_from_file``
factories use it (gain#938). ``Memo`` owns the check -> build -> store
sequence and the lock around it for all six memos.
"""
import datetime
import threading
import time

from gain.genomic_resources.memo import Memo, config_memo_key


def test_config_memo_key_accepts_a_config_json_cannot_spell() -> None:
    """A bare date and an int key next to a str key still give a key.

    ``json.dumps(config, sort_keys=True)`` raises on both: a date has no
    JSON spelling, and an int key cannot be sorted against a str one.
    The shared idiom has to be the robust one, and must still tell two
    such configs apart.
    """
    early = config_memo_key(
        {"released": datetime.date(2026, 8, 26), 1: "one"})
    late = config_memo_key(
        {"released": datetime.date(2026, 8, 27), 1: "one"})

    assert isinstance(early, str)
    assert early != late


def test_config_memo_key_ignores_the_order_keys_were_written_in() -> None:
    one_way = config_memo_key({"type": "basic", "meta": {"a": 1, "b": 2}})
    other_way = config_memo_key({"meta": {"b": 2, "a": 1}, "type": "basic"})

    assert one_way == other_way


def test_two_threads_racing_on_one_key_build_once() -> None:
    """The build runs under the memo's lock, so a racer waits for it.

    The first build does not return until the second thread is calling
    into the memo, and then lingers a little; were the build outside the
    lock, the second thread would start a build of its own meanwhile.
    """
    memo: Memo[str, object] = Memo()
    builds: list[object] = []
    first_build_started = threading.Event()
    second_caller_entering = threading.Event()

    def build() -> object:
        built = object()
        builds.append(built)
        first_build_started.set()
        second_caller_entering.wait(timeout=5.0)
        time.sleep(0.2)
        return built

    results: list[object] = []

    def first_call() -> None:
        results.append(memo.get_or_build("key", build))

    def second_call() -> None:
        second_caller_entering.set()
        results.append(memo.get_or_build("key", build))

    first = threading.Thread(target=first_call, daemon=True)
    second = threading.Thread(target=second_call, daemon=True)
    first.start()
    assert first_build_started.wait(timeout=5.0)
    second.start()
    first.join(timeout=5.0)
    second.join(timeout=5.0)

    assert len(builds) == 1
    assert len(results) == 2
    assert results[0] is results[1] is builds[0]
