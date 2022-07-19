from __future__ import annotations

import argparse
import os.path
import subprocess
import tempfile

MODULES = (
    '_elementtree',
    '_uuid',
    'bz2',
    'ctypes',
    'curses',
    'dbm.ndbm',
    'gzip',
    'hashlib',
    'lzma',
    'readline',
    'sqlite3',
    'ssl',
    'uuid',
    'venv',
    'zlib',
)


def test_can_import_modules(py: str) -> None:
    subprocess.check_call((py, '-c', f'import {",".join(MODULES)}'))


def test_can_make_ssl_request(py: str) -> None:
    prog = '''\
import urllib.request
urllib.request.urlopen("https://pypi.org/simple/astpretty").read()
'''
    subprocess.check_call((py, '-c', prog))


def test_curses_is_wide(py: str) -> None:
    subprocess.check_call((py, '-c', 'import curses;curses.window.get_wch'))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dist-dir', default='dist')
    args = parser.parse_args()

    if not os.path.exists(args.dist_dir) or not os.listdir(args.dist_dir):
        print('SKIP: no file to validate')
        return 0

    filename, = os.listdir(args.dist_dir)
    filename = os.path.join(args.dist_dir, filename)

    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.check_call(('tar', '-C', tmpdir, '-xf', filename))
        rootdir, = os.listdir(tmpdir)
        py = os.path.join(tmpdir, rootdir, 'bin', 'python3')

        tests = [(k, v) for k, v in globals().items() if k.startswith('test_')]
        for k, test in tests:
            print(f'{k}...', end='', flush=True)
            test(py)
            print('PASSED')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
