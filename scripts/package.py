#!/usr/bin/env python3
"""Package the standalone Bzlmod module with deterministic metadata and a checksum."""
import argparse
import gzip
import hashlib
from pathlib import Path
import re
import tarfile


def package(source: Path, output: Path) -> Path:
    version = re.search(r'version\s*=\s*"([^"]+)"', (source / "MODULE.bazel").read_text())[1]
    prefix = f"rules_compose_preview-{version}"
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"{prefix}.tar.gz"
    with archive.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for path in sorted(source.iterdir()):
                if not path.is_file() or not (path.suffix in {".bzl", ".sh"} or path.name in
                                             {"MODULE.bazel", "BUILD.bazel", "LICENSE", "README.md"}):
                    continue
                info = tar.gettarinfo(str(path), arcname=f"{prefix}/{path.relative_to(source)}")
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                with path.open("rb") as content:
                    tar.addfile(info, content)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(f"{digest}  {archive.name}\n")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path, default=Path("build/bazel-rules"))
    args = parser.parse_args()
    print(package(args.source, args.out))
