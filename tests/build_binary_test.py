from __future__ import annotations

import email.message
import http
import io
import os.path
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from unittest import mock

import pytest

import build_binary
from build_binary import Version


def test_version_parse():
    assert Version.parse('3.8.13') == Version(3, 8, 13)


def test_version_py_minor():
    assert Version(3, 10, 1).py_minor == 'python3.10'


def test_version_s():
    assert Version(3, 10, 1).s == '3.10.1'


def test_already_built_404():
    error = urllib.error.HTTPError(
        'https://example.com',
        404,
        http.HTTPStatus(404).phrase,
        email.message.Message(),
        io.BytesIO(b''),
    )
    filename = 'python-3.8.13-manylinux_2_24_x86_64.tgz'
    with mock.patch.object(urllib.request, 'urlopen', side_effect=error):
        assert build_binary.already_built(filename) is False


def test_already_built_reraises_unknown_errors():
    error = urllib.error.HTTPError(
        'https://example.com',
        500,
        http.HTTPStatus(500).phrase,
        email.message.Message(),
        io.BytesIO(b''),
    )
    filename = 'python-3.8.13-manylinux_2_24_x86_64.tgz'
    with mock.patch.object(urllib.request, 'urlopen', side_effect=error):
        with pytest.raises(urllib.error.HTTPError):
            build_binary.already_built(filename)


def test_already_built_exists():
    resp = io.BytesIO(b'')
    filename = 'python-3.8.13-manylinux_2_24_x86_64.tgz'
    with mock.patch.object(urllib.request, 'urlopen', return_value=resp):
        assert build_binary.already_built(filename) is True


def test_archive_name():
    version = Version(3, 9, 11)
    archive = build_binary._archive_name(version, 2, 'macosx_10_15_x86_64')
    assert archive == 'python-3.9.11+2-macosx_10_15_x86_64.tgz'


def test_docker_run_podman():
    with mock.patch.object(shutil, 'which', return_value='/usr/bin/podman'):
        assert build_binary._docker_run() == ('podman', 'run')


def test_docker_run_docker():
    with mock.patch.object(shutil, 'which', return_value=None):
        with mock.patch.object(os, 'getuid', return_value=1000):
            with mock.patch.object(os, 'getgid', return_value=1000):
                ret = build_binary._docker_run()
    assert ret == ('docker', 'run', '--user', '1000:1000')


def test_linux_configure_args():
    assert build_binary._linux_configure_args() == ()


def test_linux_modify_environ():
    env = {'SOME': 'VARIABLE'}
    build_binary._linux_modify_env(env)
    assert env == {'SOME': 'VARIABLE'}


def test_libc6_links():
    out = b'''\
/.
/etc
/etc/ld.so.conf.d/
/etc/ld.so.conf.d/aarch64-linux-gnu.conf
/lib
/lib/aarch64-linux-gnu
/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1
/lib/aarch64-linux-gnu/libc.so.6
/usr/lib/aarch64-linux-gnu/gconv/BIG5.so
/usr/share/doc/libc6/NEWS.gz
/lib/ld-linux-aarch64.so.1
'''
    with mock.patch.object(subprocess, 'check_output', return_value=out):
        ret = build_binary._libc6_links.__wrapped__()

    assert ret == frozenset((
        '/lib/aarch64-linux-gnu/libc.so.6',
        '/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1',
        '/lib/ld-linux-aarch64.so.1',
    ))


@pytest.fixture
def patched_libc6_links():
    ret = frozenset((
        '/lib/aarch64-linux-gnu/libm.so.6',
        '/lib/aarch64-linux-gnu/libc.so.6',
        '/lib/ld-linux-aarch64.so.1',
    ))
    with mock.patch.object(build_binary, '_libc6_links', return_value=ret):
        yield


def test_linux_linked_unit(patched_libc6_links):
    out = b'''\
\tlinux-vdso.so.1 (0x0000ffff9f326000)
\tlibm.so.6 => /lib/aarch64-linux-gnu/libm.so.6 (0x0000ffff9ec70000)
\tlibexpat.so.1 => /lib/aarch64-linux-gnu/libexpat.so.1 (0x0000ffff9ec30000)
\tlibz.so.1 => /lib/aarch64-linux-gnu/libz.so.1 (0x0000ffff9ec00000)
\tlibc.so.6 => /lib/aarch64-linux-gnu/libc.so.6 (0x0000ffff9ea50000)
\t/lib/ld-linux-aarch64.so.1 (0x0000ffff9f2ed000)
\t/lib64/ld-linux-aarch64.so.1 (0x0000ffff9f2ed000)
'''
    with mock.patch.object(subprocess, 'check_output', return_value=out):
        ret = build_binary._linux_linked('/some/file.so')
    assert ret == [
        '/lib/aarch64-linux-gnu/libexpat.so.1',
        '/lib/aarch64-linux-gnu/libz.so.1',
    ]


def test_linux_linked_unit_static_linked(patched_libc6_links):
    out = b'\tstatically linked\n'
    with mock.patch.object(subprocess, 'check_output', return_value=out):
        ret = build_binary._linux_linked('/some/file.so')
    assert ret == []


def test_linux_platform_name():
    libc_ret = ('glibc', '2.35')
    with mock.patch.object(platform, 'libc_ver', return_value=libc_ret):
        with mock.patch.object(platform, 'machine', return_value='x86_64'):
            ret = build_binary._linux_platform_name()

    assert ret == 'manylinux_2_35_x86_64'


def test_brew_paths():
    out = b'''\
/opt/homebrew/opt/openssl@1.1
/opt/homebrew/opt/xz
'''
    with mock.patch.object(subprocess, 'check_output', return_value=out):
        ret = build_binary._brew_paths('openssl@1.1', 'xz')
    assert ret == ['/opt/homebrew/opt/openssl@1.1', '/opt/homebrew/opt/xz']


@pytest.fixture
def fake_brew_paths():
    def _brew_paths(*pkgs: str) -> list[str]:
        return [f'/opt/homebrew/opt/{pkg}' for pkg in pkgs]
    with mock.patch.object(build_binary, '_brew_paths', _brew_paths):
        yield


def test_darwin_configure_args(fake_brew_paths):
    assert build_binary._darwin_configure_args() == (
        '--with-openssl=/opt/homebrew/opt/openssl@1.1',
    )


def test_darwin_modify_env(fake_brew_paths):
    env = {'SOME': 'VARIABLE'}
    with mock.patch.object(build_binary, 'BREW_LIBS', ('ncurses', 'xz')):
        build_binary._darwin_modify_env(env)
    assert env == {
        'SOME': 'VARIABLE',
        'CPPFLAGS': '-I/opt/homebrew/opt/ncurses/include -I/opt/homebrew/opt/xz/include',  # noqa: E501
        'LDFLAGS': '-L/opt/homebrew/opt/ncurses/lib -L/opt/homebrew/opt/xz/lib',  # noqa: E501
        'PKG_CONFIG_PATH': '/opt/homebrew/opt/ncurses/lib/pkgconfig:/opt/homebrew/opt/xz/lib/pkgconfig',  # noqa: E501
    }


@pytest.fixture
def openssl_dir(tmp_path):
    openssl_dir = tmp_path.joinpath('openssl@1.1/lib')
    openssl_dir.mkdir(parents=True)
    libssl = openssl_dir.joinpath('libssl.1.1.dylib')
    libssl.touch()
    libcrypto = openssl_dir.joinpath('libcrypto.1.1.dylib')
    libcrypto.touch()

    return str(libssl), str(libcrypto)


def test_darwin_linked_unit(tmp_path, openssl_dir):
    libssl, libcrypto = openssl_dir

    lib_dynload_dir = tmp_path.joinpath('lib/python3.8/lib-dynload')
    lib_dynload_dir.mkdir(parents=True)
    so = lib_dynload_dir.joinpath('_ssl.cpython-38-darwin.so')
    so.touch()

    out = f'''\
{so}:
\t{libssl} (compatibility version 1.1.0, current version 1.1.0)
\t{libcrypto} (compatibility version 1.1.0, current version 1.1.0)
\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1311.100.3)
'''.encode()  # noqa: E501
    with mock.patch.object(subprocess, 'check_output', return_value=out):
        ret = build_binary._darwin_linked(str(so))

    assert ret == [libssl, libcrypto]


def test_darwin_linked_unit_self_linked(tmp_path, openssl_dir):
    libssl, libcrypto = openssl_dir

    # for some reason `otool -L` thinks libssl links itself?
    out = f'''\
{libssl}:
\t{libssl} (compatibility version 1.1.0, current version 1.1.0)
\t{libcrypto} (compatibility version 1.1.0, current version 1.1.0)
\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1311.100.3)
'''.encode()  # noqa: E501
    with mock.patch.object(subprocess, 'check_output', return_value=out):
        ret = build_binary._darwin_linked(libssl)

    assert ret == [libcrypto]


def test_darwin_platform_name():
    mac_ver = ('10.15', ('', '', ''), 'x86_64')
    with mock.patch.object(platform, 'mac_ver', return_value=mac_ver):
        with mock.patch.object(platform, 'machine', return_value='x86_64'):
            ret = build_binary._darwin_platform_name()

    assert ret == 'macosx_10_15_x86_64'


def test_darwin_platform_name_newer_than_11():
    # compatibility version for macos>=11 is now major version only
    mac_ver = ('12.4', ('', '', ''), 'arm64')
    with mock.patch.object(platform, 'mac_ver', return_value=mac_ver):
        with mock.patch.object(platform, 'machine', return_value='arm64'):
            ret = build_binary._darwin_platform_name()

    assert ret == 'macosx_12_0_arm64'


def test_darwin_platform_name_three_part_version():
    mac_ver = ('11.6.6', ('', '', ''), 'x86_64')
    with mock.patch.object(platform, 'mac_ver', return_value=mac_ver):
        with mock.patch.object(platform, 'machine', return_value='x86_64'):
            ret = build_binary._darwin_platform_name()

    assert ret == 'macosx_11_0_x86_64'


def test_sanitize_environ():
    env = {
        'PATH': '/home/asottile/bin:/usr/bin:/bin',
        'CFLAGS': '-Ibroken',
        'CPPFLAGS': '-Ibroken2',
        'LDFLAGS': '-lmylib',
        'PKG_CONFIG_PATH': '/home/asottile/pkg-config',
        'SOME': 'VARIABLE',
    }
    build_binary._sanitize_environ(env)
    assert env == {
        'PATH': '/home/asottile/bin:/usr/bin:/bin',
        'HOMEBREW_NO_AUTO_UPDATE': '1',
        'SOME': 'VARIABLE',
    }


def test_extract_strip_1(tmp_path):
    in_dir = tmp_path.joinpath('in_dir')
    in_dir.mkdir()
    in_dir.joinpath('root-file').touch()

    tgz = tmp_path.joinpath('out.tgz')

    build_binary._archive(str(in_dir), str(tgz))

    assert tgz.is_file()

    build_binary._extract_strip_1(str(tgz), str(tmp_path))

    assert tmp_path.joinpath('root-file').is_file()


def test_relink_integration(tmp_path):
    shared_suffix = 'dylib' if sys.platform == 'darwin' else 'so'

    mylib_h_src = '''\
#pragma once
int mylib_version(void);
'''
    mylib_c_src = '''\
int mylib_version(void) {
    return 9001;
}
'''
    main_c_src = '''\
#include <stdio.h>
#include <mylib.h>

int main(void) {
    printf("hello from %d\\n", mylib_version());
}
'''

    homebrew_dir = tmp_path.joinpath('homebrew')
    lib = homebrew_dir.joinpath('lib')
    lib.mkdir(parents=True)
    libmylib = lib.joinpath(f'libmylib.{shared_suffix}')
    include = homebrew_dir.joinpath('include')
    include.mkdir(parents=True)
    include.joinpath('mylib.h').write_text(mylib_h_src)

    src_dir = tmp_path.joinpath('src')
    src_dir.mkdir(parents=True)
    mylib_c = src_dir.joinpath('mylib.c')
    mylib_c.write_text(mylib_c_src)
    main_c = src_dir.joinpath('main.c')
    main_c.write_text(main_c_src)

    prefix = tmp_path.joinpath('prefix')
    prefix.mkdir(parents=True)
    prefix_lib = prefix.joinpath('lib')
    prefix_lib.mkdir(parents=True)
    main = prefix.joinpath('main')

    newprefix = tmp_path.joinpath('newprefix')
    newmain = newprefix.joinpath('main')

    with mock.patch.dict(os.environ, {'LD_LIBRARY_PATH': str(lib)}):
        subprocess.check_call(('gcc', '-shared', '-o', libmylib, mylib_c))

        subprocess.check_call((
            'gcc',
            f'-I{include}',
            f'-L{lib}',
            '-o', main,
            main_c,
            '-lmylib',
        ))

        # make sure we built correctly
        assert subprocess.check_output(main) == b'hello from 9001\n'

        assert build_binary.plat.linked(str(main)) == [str(libmylib)]

        build_binary._relink_1(str(main), str(prefix_lib))

    assert prefix_lib.joinpath(f'libmylib.{shared_suffix}').exists()

    # should still work after we remove the original lib
    shutil.rmtree(homebrew_dir)
    assert subprocess.check_output(main) == b'hello from 9001\n'

    # should still work after we relocate the prefix
    prefix.rename(newprefix)
    assert subprocess.check_output(newmain) == b'hello from 9001\n'
