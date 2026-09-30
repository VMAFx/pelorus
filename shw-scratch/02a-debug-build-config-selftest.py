"""shw-1: reproduce the build-config-sync self-test failures seen on MSYS2 UCRT64.

Runs the same git invocations the self-test makes, from a native (UCRT64) Python,
and prints the raw results so the Windows-specific cause is visible.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

print("python:", sys.executable, sys.version)
print("git on PATH:", shutil.which("git"))

def run(*cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(f"$ {' '.join(cmd)}\n  rc={r.returncode}\n  out={r.stdout.strip()!r}\n  err={r.stderr.strip()!r}")
    return r

with tempfile.TemporaryDirectory(prefix="pel-dbg-") as d:
    print("tempdir:", d)
    run("git", "init", "-q", d)
    (Path(d) / "a.txt").write_text("x\n", encoding="utf-8")
    run("git", "-C", d, "add", "a.txt")
    run("git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t.invalid",
        "-c", "core.hooksPath=/dev/null", "commit", "--no-verify", "-qm", "fixture")
    run("git", "-C", d, "branch", "n1.2.3")
    run("git", "-C", d, "rev-parse", "--verify", "n1.2.3^{commit}")
    run("git", "-C", d, "rev-parse", "--verify", "n1.2.3^{tree}")
    run("git", "-C", d, "rev-parse", "--verify", "n1.2.3")
    run("git", "-C", d, "rev-parse", "--verify", "refs/tags/n1.2.3^{commit}")
    # echo through the MSYS runtime to see what argv the child actually got
    run("C:/msys64/usr/bin/echo.exe", "n1.2.3^{commit}", "a{b,c}d", "{x}")

    # git am hook fixture: format-patch -> write_text (CRLF on Windows?) -> am
    (Path(d) / "a.txt").write_text("x\ny\n", encoding="utf-8")
    run("git", "-C", d, "add", "a.txt")
    run("git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t.invalid",
        "-c", "core.hooksPath=/dev/null", "commit", "--no-verify", "-qm", "patch")
    fp = run("git", "-C", d, "format-patch", "-1", "--stdout")
    patch = Path(d).parent / (Path(d).name + ".patch")
    patch.write_text(fp.stdout, encoding="utf-8")
    raw = patch.read_bytes()
    print("patch CRLF count:", raw.count(b"\r\n"), "LF count:", raw.count(b"\n"))
    wt = Path(d).parent / (Path(d).name + "-wt")
    run("git", "-C", d, "-c", "core.hooksPath=/dev/null", "worktree", "add", "--detach", str(wt), "HEAD~1")
    run("git", "-C", str(wt), "-c", "core.hooksPath=/dev/null", "-c", "user.name=t",
        "-c", "user.email=t@t.invalid", "am", "--3way", str(patch))
    print("patched content:", (wt / "a.txt").read_bytes())
    run("git", "-C", d, "worktree", "remove", "--force", str(wt))
    patch.unlink()
