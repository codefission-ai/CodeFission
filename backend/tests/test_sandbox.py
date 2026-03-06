"""Tests for Landlock filesystem sandbox."""

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Backend dir for subprocess sys.path injection
BACKEND_DIR = str(Path(__file__).resolve().parent.parent)

from services.sandbox import (
    check_available,
    apply_sandbox,
    install_hook,
    set_sandbox,
    clear_sandbox,
    default_writable_paths,
)


@pytest.fixture(autouse=True)
def _reset_sandbox():
    yield
    clear_sandbox()


def _run_sandboxed_script(script: str, **kwargs) -> subprocess.CompletedProcess:
    """Run a Python script in a subprocess."""
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=10,
        **kwargs,
    )


class TestLandlockAvailable:
    def test_available_on_this_kernel(self):
        assert check_available() is True


class TestApplySandbox:
    """Test Landlock enforcement via subprocess (to avoid restricting test process)."""

    def test_write_allowed_in_writable_dir(self):
        with tempfile.TemporaryDirectory() as writable:
            target = os.path.join(writable, "test.txt")
            r = _run_sandboxed_script(f"""\
import sys; sys.path.insert(0, {BACKEND_DIR!r})
from services.sandbox import apply_sandbox
apply_sandbox([{writable!r}, '/tmp', '/dev'])
with open({target!r}, 'w') as f:
    f.write('hello')
print('OK')
""")
            assert r.returncode == 0
            assert "OK" in r.stdout

    def test_write_blocked_outside_writable_dir(self):
        with tempfile.TemporaryDirectory() as writable, \
             tempfile.TemporaryDirectory() as forbidden:
            target = os.path.join(forbidden, "test.txt")
            r = _run_sandboxed_script(f"""\
import sys; sys.path.insert(0, {BACKEND_DIR!r})
from services.sandbox import apply_sandbox
apply_sandbox([{writable!r}])
try:
    with open({target!r}, 'w') as f:
        f.write('hello')
    print('WROTE')
except PermissionError:
    print('BLOCKED')
""")
            assert r.returncode == 0
            assert "BLOCKED" in r.stdout

    def test_read_allowed_everywhere(self):
        with tempfile.TemporaryDirectory() as writable:
            # Create a readable file outside writable dir
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                f.write('readable_content')
                readable = f.name
            try:
                r = _run_sandboxed_script(f"""\
import sys; sys.path.insert(0, {BACKEND_DIR!r})
from services.sandbox import apply_sandbox
apply_sandbox([{writable!r}])
print(open({readable!r}).read())
""")
                assert r.returncode == 0
                assert "readable_content" in r.stdout
            finally:
                os.unlink(readable)

    def test_mkdir_blocked_outside_writable(self):
        with tempfile.TemporaryDirectory() as writable, \
             tempfile.TemporaryDirectory() as forbidden:
            target = os.path.join(forbidden, "newdir")
            r = _run_sandboxed_script(f"""\
import sys, os; sys.path.insert(0, {BACKEND_DIR!r})
from services.sandbox import apply_sandbox
apply_sandbox([{writable!r}])
try:
    os.mkdir({target!r})
    print('CREATED')
except PermissionError:
    print('BLOCKED')
""")
            assert r.returncode == 0
            assert "BLOCKED" in r.stdout

    def test_delete_blocked_outside_writable(self):
        with tempfile.TemporaryDirectory() as writable, \
             tempfile.TemporaryDirectory() as forbidden:
            target = os.path.join(forbidden, "victim.txt")
            Path(target).write_text("important data")
            r = _run_sandboxed_script(f"""\
import sys, os; sys.path.insert(0, {BACKEND_DIR!r})
from services.sandbox import apply_sandbox
apply_sandbox([{writable!r}])
try:
    os.unlink({target!r})
    print('DELETED')
except PermissionError:
    print('BLOCKED')
""")
            assert r.returncode == 0
            assert "BLOCKED" in r.stdout
            assert Path(target).exists()  # File still there

    def test_sandbox_inherited_by_child_process(self):
        """Landlock restrictions survive fork+exec — grandchild is also sandboxed."""
        with tempfile.TemporaryDirectory() as writable, \
             tempfile.TemporaryDirectory() as forbidden:
            target = os.path.join(forbidden, "test.txt")
            r = _run_sandboxed_script(f"""\
import sys, subprocess; sys.path.insert(0, {BACKEND_DIR!r})
from services.sandbox import apply_sandbox
apply_sandbox([{writable!r}])
# Spawn a grandchild that tries to write
result = subprocess.run(
    [sys.executable, '-c', '''
try:
    open({target!r}, 'w').write('hello')
    print('WROTE')
except PermissionError:
    print('BLOCKED')
'''],
    capture_output=True, text=True,
)
print(result.stdout.strip())
""")
            assert r.returncode == 0
            assert "BLOCKED" in r.stdout


class TestAsyncHook:
    """Test the asyncio.create_subprocess_exec monkey-patch."""

    def _run_async(self, coro):
        """Run an async function — works whether or not uvloop is the default."""
        return asyncio.run(coro)

    def test_hook_sandboxes_subprocess(self):
        install_hook()
        with tempfile.TemporaryDirectory() as writable, \
             tempfile.TemporaryDirectory() as forbidden:
            target = os.path.join(forbidden, "test.txt")

            async def run():
                set_sandbox([writable])
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-c",
                    f"import os\n"
                    f"try:\n"
                    f"    open('{target}', 'w').write('hello')\n"
                    f"    print('WROTE')\n"
                    f"except PermissionError:\n"
                    f"    print('BLOCKED')\n",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                clear_sandbox()
                return stdout.decode()

            result = self._run_async(run())
            assert "BLOCKED" in result

    def test_hook_allows_writable_path(self):
        install_hook()
        with tempfile.TemporaryDirectory() as writable:
            target = os.path.join(writable, "test.txt")

            async def run():
                set_sandbox([writable, "/tmp", "/dev"])
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-c",
                    f"open('{target}', 'w').write('hello')\nprint('OK')\n",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                clear_sandbox()
                return stdout.decode()

            result = self._run_async(run())
            assert "OK" in result

    def test_no_sandbox_without_contextvar(self):
        install_hook()
        clear_sandbox()
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "test.txt")

            async def run():
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-c",
                    f"open('{target}', 'w').write('hello')\nprint('OK')\n",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                return stdout.decode()

            result = self._run_async(run())
            assert "OK" in result

    def test_sandbox_only_affects_current_context(self):
        """Setting sandbox in one context doesn't affect subprocesses spawned
        from a clean context."""
        install_hook()
        with tempfile.TemporaryDirectory() as writable, \
             tempfile.TemporaryDirectory() as target_dir:
            target = os.path.join(target_dir, "test.txt")

            async def run():
                set_sandbox([writable])
                clear_sandbox()
                # Now subprocess should NOT be sandboxed
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-c",
                    f"open('{target}', 'w').write('hello')\nprint('OK')\n",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                return stdout.decode()

            result = self._run_async(run())
            assert "OK" in result

    def test_sandbox_propagates_through_nested_tasks(self):
        """Sandbox set in parent task propagates to child tasks."""
        install_hook()
        with tempfile.TemporaryDirectory() as writable, \
             tempfile.TemporaryDirectory() as forbidden:
            target = os.path.join(forbidden, "test.txt")

            async def run():
                result_holder = {}

                async def outer():
                    set_sandbox([writable])

                    async def inner():
                        proc = await asyncio.create_subprocess_exec(
                            sys.executable, "-c",
                            f"try:\n"
                            f"    open('{target}', 'w').write('hello')\n"
                            f"    print('WROTE')\n"
                            f"except PermissionError:\n"
                            f"    print('BLOCKED')\n",
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        stdout, _ = await proc.communicate()
                        result_holder["out"] = stdout.decode()

                    await asyncio.create_task(inner())
                    clear_sandbox()

                await asyncio.create_task(outer())
                return result_holder.get("out", "")

            result = self._run_async(run())
            assert "BLOCKED" in result

    def test_sandbox_works_with_uvloop(self):
        """Sandbox works when uvloop is the event loop (the original bug)."""
        try:
            import uvloop
        except ImportError:
            pytest.skip("uvloop not installed")

        install_hook()
        with tempfile.TemporaryDirectory() as writable, \
             tempfile.TemporaryDirectory() as forbidden:
            target = os.path.join(forbidden, "test.txt")

            async def run():
                set_sandbox([writable])
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-c",
                    f"try:\n"
                    f"    open('{target}', 'w').write('hello')\n"
                    f"    print('WROTE')\n"
                    f"except PermissionError:\n"
                    f"    print('BLOCKED')\n",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                clear_sandbox()
                return stdout.decode()

            result = uvloop.run(run())
            assert "BLOCKED" in result, f"Sandbox failed with uvloop! Got: {result}"


class TestDefaultWritablePaths:
    def test_includes_workspace_dir(self):
        paths = default_writable_paths("/data/workspaces/tree123")
        assert "/data/workspaces/tree123" in paths

    def test_includes_tmp(self):
        paths = default_writable_paths("/any")
        assert "/tmp" in paths

    def test_includes_claude_dir(self):
        paths = default_writable_paths("/any")
        home = str(Path.home())
        assert f"{home}/.claude" in paths
