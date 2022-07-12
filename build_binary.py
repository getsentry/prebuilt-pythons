from __future__ import annotations

import argparse
import functools
import gzip
import hashlib
import multiprocessing
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from typing import Callable
from typing import MutableMapping
from typing import NamedTuple
from typing import Protocol


class Version(NamedTuple):
    major: int
    minor: int
    patch: int

    @property
    def py_minor(self) -> str:
        return f'python{self.major}.{self.minor}'

    @property
    def s(self) -> str:
        return f'{self.major}.{self.minor}.{self.patch}'

    @classmethod
    def parse(cls, s: str) -> Version:
        major_s, minor_s, patch_s = s.split('.')
        return cls(int(major_s), int(minor_s), int(patch_s))


class Python(NamedTuple):
    url: str
    sha256: str


PYTHONS = {
    Version(3, 8, 13): Python(
        url='https://www.python.org/ftp/python/3.8.13/Python-3.8.13.tar.xz',
        sha256='6f309077012040aa39fe8f0c61db8c0fa1c45136763299d375c9e5756f09cf57',  # noqa: E501
    ),
    Version(3, 9, 13): Python(
        url='https://www.python.org/ftp/python/3.9.13/Python-3.9.13.tar.xz',
        sha256='125b0c598f1e15d2aa65406e83f792df7d171cdf38c16803b149994316a3080f',  # noqa: E501
    ),
    Version(3, 10, 5): Python(
        url='https://www.python.org/ftp/python/3.10.5/Python-3.10.5.tar.xz',
        sha256='8437efd5b106ef0a75aabfbf23d880625120a73a86a22ade4d2e2e68d7b74486',  # noqa: E501
    ),
}


def already_built(archive_name: str) -> bool:
    req = urllib.request.Request(
        f'https://storage.googleapis.com/sentry-dev-infra-assets/prebuilt-pythons/{archive_name}',  # noqa: E501
        method='HEAD',
    )
    try:
        urllib.request.urlopen(req)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        else:
            raise
    else:
        return True


IMAGE_NAME = f'ghcr.io/getsentry/prebuilt-pythons-manylinux-{platform.machine()}-ci'  # noqa: E501


def _docker_run() -> tuple[str, ...]:
    if shutil.which('podman'):
        return ('podman', 'run')
    else:
        return ('docker', 'run', '--user', f'{os.getuid()}:{os.getgid()}')


def _linux_setup_deps(version: Version) -> int:
    # already exec'd our container
    if os.environ.get('BUILD_BINARY_IN_CONTAINER'):
        return 0

    print('execing into container...')
    cmd = (
        *_docker_run(),
        '--pull=always',
        '--rm',
        '--volume', f'{os.path.abspath("dist")}:/dist:rw',
        # TODO: if we target 3.9+: __file__ is an abspath
        '--volume', f'{os.path.abspath(__file__)}:/{os.path.basename(__file__)}',  # noqa: E501
        '--workdir', '/',
        IMAGE_NAME,
        'python3', '-um', 'build_binary',
        version.s,
    )
    os.execvp(cmd[0], cmd)


def _linux_configure_args() -> tuple[str, ...]:
    return ()  # no special args needed on linux


def _linux_modify_env(environ: MutableMapping[str, str]) -> None:
    pass  # no special environ mutation needed on linux


@functools.lru_cache(maxsize=1)
def _libc6_links() -> frozenset[str]:
    out = subprocess.check_output(('dpkg', '-L', 'libc6')).decode()
    return frozenset(
        line for line in out.splitlines()
        if line.startswith('/lib/')
        if '.so' in line
    )


LDD_LINE = re.compile(r'^[^ ]+ => ([^ ]+) \([^(]+\)$')


def _linux_linked(filename: str) -> list[str]:
    ignored = _libc6_links()

    out = subprocess.check_output(('ldd', filename)).decode()
    ret = []
    for line in out.splitlines():
        line = line.strip()
        match = LDD_LINE.match(line)

        if match is None:
            if line == 'statically linked':
                continue
            elif line.startswith((
                    'linux-vdso.so.1 ',
                    '/lib/ld-linux-',
                    '/lib64/ld-linux-',
            )):
                continue
            else:
                raise AssertionError(f'unexpected ldd line:\n\n{line}')
        elif match[1] not in ignored:
            ret.append(match[1])

    return ret


def _linux_relink(filename: str, libdir: str, *, set_name: bool) -> None:
    origin = f'$ORIGIN/{os.path.relpath(libdir, os.path.dirname(filename))}'
    cmd = ('patchelf', '--force-rpath', '--set-rpath', origin, filename)
    subprocess.check_call(cmd)


def _linux_archive_name(version: Version) -> str:
    _, libc = platform.libc_ver()
    libc = libc.replace('.', '_')
    return f'python-{version.s}-manylinux_{libc}_{platform.machine()}.tgz'


BREW_SSL = 'openssl@1.1'
BREW_LIBS = ('ncurses', 'sqlite', 'xz')


def _brew() -> str:
    if platform.machine() == 'arm64':
        return '/opt/homebrew/bin/brew'
    else:
        return '/usr/local/bin/brew'


def _darwin_setup_deps(version: Version) -> int:
    if not os.access(_brew(), os.X_OK):
        raise NotImplementedError('setup brew')

    pkgs = ('pkg-config', BREW_SSL, *BREW_LIBS)
    return subprocess.call((_brew(), 'install', '-q', *pkgs))


def _brew_paths(*pkgs: str) -> list[str]:
    cmd = (_brew(), '--prefix', *pkgs)
    return subprocess.check_output(cmd).decode().splitlines()


def _darwin_configure_args() -> tuple[str, ...]:
    ssl_path, = _brew_paths(BREW_SSL)
    return (f'--with-openssl={ssl_path}',)


def _darwin_modify_env(environ: MutableMapping[str, str]) -> None:
    brew_paths = _brew_paths(*BREW_LIBS)

    def _paths(*parts: str) -> list[str]:
        return [os.path.join(path, *parts) for path in brew_paths]

    environ['CPPFLAGS'] = ' '.join(f'-I{path}' for path in _paths('include'))
    environ['LDFLAGS'] = ' '.join(f'-L{path}' for path in _paths('lib'))
    environ['PKG_CONFIG_PATH'] = ':'.join(_paths('lib', 'pkgconfig'))


OTOOL_L_LINE = re.compile(r'\s+(.+) \(compatibility .*, current .*\)$')


def _darwin_linked(filename: str) -> list[str]:
    out = subprocess.check_output(('otool', '-L', filename)).decode()
    lines = out.splitlines()
    if lines[0] != f'{filename}:':
        raise AssertionError(f'unexpected otool output:\n\n{out}')

    ret = []

    # every line after this should be linker output
    for line in lines[1:]:
        match = OTOOL_L_LINE.match(line)
        if match is None:
            raise AssertionError(f'unexpected otool output:\n\n{line}')

        # otool -L sometimes marks files as linking themselves?
        if match[1] == filename:
            continue
        # mach-o "magics" a lot of libraries into existence
        # we're only concerned with ones that are actually on disk
        elif os.path.isfile(match[1]):
            ret.append(match[1])

    return ret


def _darwin_relink(filename: str, libdir: str, *, set_name: bool) -> None:
    dirname, basename = os.path.split(filename)
    if set_name:
        subprocess.check_call((
            'install_name_tool',
            '-id', f'@loader_path/{basename}',
            filename,
        ))

    for link in _darwin_linked(filename):
        soname = os.path.basename(link)
        libdir_so = os.path.join(libdir, soname)
        new = f'@loader_path/{os.path.relpath(libdir_so, dirname)}'
        relink_cmd = ('install_name_tool', '-change', link, new, filename)
        subprocess.check_call(relink_cmd)

    # without code signing, SIP will SIGKILL the process
    # '-' is the "adhoc signature"
    subprocess.check_call(('codesign', '--force', '--sign', '-', filename))


def _darwin_archive_name(version: Version) -> str:
    # TODO: once on 3.9+ we can target lower mac versions (weak linking)
    macos, _, _ = platform.mac_ver()
    major, minor, *_ = macos.split('.')
    if int(major) >= 11:
        minor = '0'
    macos = f'{major}_{minor}'
    return f'python-{version.s}-macosx_{macos}_{platform.machine()}.tgz'


class _Relink(Protocol):
    def __call__(self, filename: str, libdir: str, *, set_name: bool) -> None:
        ...


class Platform(NamedTuple):
    setup_deps: Callable[[Version], int]
    configure_args: Callable[[], tuple[str, ...]]
    modify_env: Callable[[MutableMapping[str, str]], None]
    linked: Callable[[str], list[str]]
    relink: _Relink
    archive_name: Callable[[Version], str]


plats = {
    'linux': Platform(
        setup_deps=_linux_setup_deps,
        configure_args=_linux_configure_args,
        modify_env=_linux_modify_env,
        linked=_linux_linked,
        relink=_linux_relink,
        archive_name=_linux_archive_name,
    ),
    'darwin': Platform(
        setup_deps=_darwin_setup_deps,
        configure_args=_darwin_configure_args,
        modify_env=_darwin_modify_env,
        linked=_darwin_linked,
        relink=_darwin_relink,
        archive_name=_darwin_archive_name,
    ),
}
plat = plats[sys.platform]


def _sanitize_environ(environ: MutableMapping[str, str]) -> None:
    # remove any environ variables that may interfere with build
    for k in ('CFLAGS', 'CPPFLAGS', 'LDFLAGS', 'PKG_CONFIG_PATH'):
        environ.pop(k, None)

    # prevent homebrew from wasting time updating
    environ['HOMEBREW_NO_AUTO_UPDATE'] = '1'
    # set PATH to a minimal path (avoid homebrew / local installs)
    environ['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'


def _download(py: Python, target: str) -> None:
    req = urllib.request.urlopen(py.url)
    with open(target, 'wb') as f:
        shutil.copyfileobj(req, f)

    checksum = hashlib.sha256()
    with open(target, 'rb') as f:
        bts = f.read(4096)
        while bts:
            checksum.update(bts)
            bts = f.read(4096)

    if not secrets.compare_digest(checksum.hexdigest(), py.sha256):
        raise SystemExit(
            f'checksum mismatch:\n'
            f'- got: {checksum.hexdigest()}\n'
            f'- expected: {py.sha256}\n',
        )


def _extract_strip_1(tgz: str, target: str) -> None:
    # `tar --strip-components=1` would be faster
    # but requires gnu tar (not reliably available on macos)
    os.makedirs(target, exist_ok=True)
    with tarfile.open(tgz) as tarf:
        members = []
        for member in tarf.getmembers():
            _, _, member.path = member.path.partition('/')
            members.append(member)
        tarf.extractall(target, members=members)


def _build(build_dir: str, prefix: str) -> int:
    if subprocess.call(
        (
            './configure',
            '--prefix', prefix,
            '--without-ensurepip',
            '--enable-optimizations',
            '--with-lto',
            *plat.configure_args(),
        ),
        cwd=build_dir,
    ):
        return 1

    # build is separate from install so pgo works
    build_cmd = ('make', f'-j{multiprocessing.cpu_count()}')
    if subprocess.call(build_cmd, cwd=build_dir):
        return 1
    if subprocess.call(('make', 'install'), cwd=build_dir):
        return 1

    return 0


def _clean(prefix: str, version: Version) -> None:
    # maybe look at --disable-test-modules for 3.10+
    for mod_path in (
            ('idlelib',),
            ('tkinter',),
            ('test',),
            ('ctypes', 'test'),
            ('distutils', 'tests'),
            ('lib2to3', 'tests'),
            ('unittest', 'test'),
            ('sqlite3', 'test'),
    ):
        shutil.rmtree(os.path.join(prefix, 'lib', version.py_minor, *mod_path))

    # don't bundle pyc files, they'll all be invalidated after install
    subprocess.check_call(('find', prefix, '-name', '*.pyc', '-delete'))

    # TODO: there's a few other potential savings as well:
    # - symlink libpython.a (there's 2 copies)
    # - remove some unused modules / data (pydoc, lib2to3)


def _relink_1(filename: str, libdir: str, *, set_name: bool = False) -> None:
    linked = plat.linked(filename)
    plat.relink(filename, libdir, set_name=set_name)
    # relink breadth first
    to_link = []
    for link in linked:
        soname = os.path.basename(link)
        libdir_so = os.path.join(libdir, soname)
        if not os.path.exists(libdir_so):
            shutil.copy(link, libdir)
            to_link.append(libdir_so)

    for libdir_so in to_link:
        _relink_1(libdir_so, libdir, set_name=True)


def _relink(prefix: str, version: Version) -> None:
    libdir = os.path.join(prefix, 'lib')

    _relink_1(os.path.join(prefix, 'bin', version.py_minor), libdir)

    dyn_dir = os.path.join(prefix, 'lib', version.py_minor, 'lib-dynload')
    for so in os.listdir(dyn_dir):
        _relink_1(os.path.join(dyn_dir, so), libdir)


def _reset_tarinfo(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo:
    tarinfo.uid = tarinfo.gid = 0
    tarinfo.uname = tarinfo.gname = 'root'
    tarinfo.mtime = 0
    return tarinfo


def _archive(src: str, dest: str) -> None:
    name, _ = os.path.splitext(os.path.basename(dest))
    arcs = [(name, src)]
    for root, dirs, filenames in os.walk(src):
        for filename in dirs + filenames:
            abspath = os.path.abspath(os.path.join(root, filename))
            relpath = os.path.relpath(abspath, src)
            arcs.append((os.path.join(name, relpath), abspath))
    arcs.sort()

    with gzip.GzipFile(dest, 'wb', mtime=0) as gzipf:
        # https://github.com/python/typeshed/issues/5491
        with tarfile.open(fileobj=gzipf, mode='w') as tf:  # type: ignore
            for arcname, abspath in arcs:
                tf.add(
                    abspath,
                    arcname=arcname,
                    recursive=False,
                    filter=_reset_tarinfo,
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('version')
    args = parser.parse_args()

    version = Version.parse(args.version)
    python = PYTHONS[version]

    os.makedirs('dist', exist_ok=True)
    _sanitize_environ(os.environ)
    plat.setup_deps(version)
    plat.modify_env(os.environ)

    archive_name = plat.archive_name(version)
    if already_built(archive_name):
        print('already built!')
        return 0

    with tempfile.TemporaryDirectory() as tmpdir:
        print('downloading...')
        tgz = os.path.join(tmpdir, 'python.tgz')
        _download(python, tgz)

        print('extracting...')
        build_dir = os.path.join(tmpdir, 'build')
        _extract_strip_1(tgz, build_dir)

        print('building...')
        prefix = os.path.join(tmpdir, 'prefix')
        if _build(build_dir, prefix):
            return 1

        print('cleaning...')
        _clean(prefix, version)

        print('relinking...')
        _relink(prefix, version)

        print('archiving...')
        archive = os.path.join(tmpdir, archive_name)
        _archive(prefix, archive)
        shutil.move(archive, 'dist')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
