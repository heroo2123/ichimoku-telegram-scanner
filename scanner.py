from pathlib import Path

_original_name = __name__
try:
    __name__ = "scanner_impl_runtime"
    source = (Path(__file__).resolve().parent / "scanner_impl.py").read_text(encoding="utf-8")
    exec(compile(source, str(Path(__file__).resolve().parent / "scanner_impl.py"), "exec"), globals(), globals())
finally:
    __name__ = _original_name

from v3.benchmark_digest import install as _install_benchmark_digest

_install_benchmark_digest(globals())

if __name__ == "__main__":
    raise SystemExit(main())
