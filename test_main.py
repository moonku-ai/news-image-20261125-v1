#!/usr/bin/env python3
"""Local tests for the news-image-20261125-v1 entry.

Run: python3 test_main.py
Checks: atlas loads, two runs are byte-identical, output is a 1024x1024
PNG under 1 MiB, and a run in a clean directory with no network works.
"""

import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def run_main(outdir, extra_env=None):
    env = dict(os.environ)
    env["MAKEFUN_OUTPUT_DIR"] = outdir
    if extra_env:
        env.update(extra_env)
    r = subprocess.run([sys.executable, os.path.join(HERE, "main.py")],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, "main.py failed:\n" + r.stdout + r.stderr
    return os.path.join(outdir, "entry.png")


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_atlas():
    sys.path.insert(0, HERE)
    import main as m
    atlas = m.Atlas(os.path.join(HERE, "atlas.bin"))
    assert "hero" in atlas.runs and "mast" in atlas.runs
    assert atlas.width("mast", "THE WORLD IN ONE DAY") > 400
    assert atlas.width("hero", "THE BREACH") > 300
    print("atlas: OK (%d runs)" % len(atlas.runs))


def test_determinism_and_size():
    d1 = tempfile.mkdtemp(prefix="ni25-a-")
    d2 = tempfile.mkdtemp(prefix="ni25-b-")
    try:
        p1 = run_main(d1)
        p2 = run_main(d2)
        s1, s2 = sha(p1), sha(p2)
        assert s1 == s2, "runs differ: %s vs %s" % (s1, s2)
        size = os.path.getsize(p1)
        assert size <= 1048576, "entry.png over 1 MiB: %d" % size
        data = open(p1, "rb").read()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
        w, h = struct.unpack(">II", data[16:24])
        assert (w, h) == (1024, 1024), "dims %dx%d" % (w, h)
        mode = os.stat(p1).st_mode & 0o777
        assert mode & 0o444 == 0o444, "entry.png not world-readable"
        print("determinism: OK sha256 %s" % s1)
        print("dims/size: OK %dx%d %d bytes" % (w, h, size))
    finally:
        shutil.rmtree(d1, ignore_errors=True)
        shutil.rmtree(d2, ignore_errors=True)


def test_clean_dir():
    tmp = tempfile.mkdtemp(prefix="ni25-clean-")
    try:
        out = os.path.join(tmp, "out")
        p = run_main(out)
        assert os.path.exists(p), "no entry.png in clean dir"
        print("clean-dir: OK")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_atlas()
    test_determinism_and_size()
    test_clean_dir()
    print("ALL TESTS PASSED")
