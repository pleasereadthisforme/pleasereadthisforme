"""현재 OS/JDK에 맞는 동봉 런타임과 빌드 지문을 만든다."""
import hashlib
import importlib.metadata
import importlib.resources
import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

import pypdfium2 as pdfium

MODULES = "java.base,java.compiler,java.desktop,java.management,java.sql"


def main():
    root = Path(__file__).resolve().parent
    output = root / "build"
    jlink = Path(shutil.which("jlink")).resolve()
    jdk = jlink.parent.parent
    with tempfile.TemporaryDirectory(prefix=".runtime-", dir=root) as tmp:
        stage = Path(tmp)
        subprocess.run([str(jlink), "--module-path", str(jdk / "jmods"), "--add-modules", MODULES,
                        "--strip-debug", "--no-header-files", "--no-man-pages", "--compress", "zip-6",
                        "--generate-cds-archive", "--output", str(stage / "jre")], check=True)
        jar = importlib.resources.files("opendataloader_pdf").joinpath("jar/opendataloader-pdf-cli.jar")
        with importlib.resources.as_file(jar) as source:
            shutil.copy2(source, stage / "parser.jar")
        java = stage / "jre/bin" / ("java.exe" if os.name == "nt" else "java")
        tiny = stage / "tiny.pdf"
        with pdfium.PdfDocument.new() as doc:
            page = doc.new_page(595, 842)
            page.close()
            doc.save(tiny)
        subprocess.run([str(java), "-Djava.awt.headless=true", "-Xmx1536m",
                        f"-XX:ArchiveClassesAtExit={stage / 'app-cds.jsa'}", "-jar", str(stage / "parser.jar"),
                        str(tiny), "--output-dir", str(stage / "warmup"), "--format", "json",
                        "--image-output", "off", "--quiet"], check=True, timeout=300, capture_output=True)
        version = subprocess.run([str(java), "-version"], capture_output=True, text=True, check=True).stderr
        legal = stage / "legal"
        legal.mkdir()
        for package in ("opendataloader-pdf", "pdfplumber", "pypdf"):
            dist = importlib.metadata.distribution(package)
            for name in dist.files or []:
                if any(tag in str(name).upper() for tag in ("LICENSE", "NOTICE", "COPYING")):
                    src = Path(dist.locate_file(name))
                    if src.is_file():
                        dest = legal / package / str(name)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dest)
        manifest = {"os": platform.system(), "machine": platform.machine(), "jdk": version,
                    "modules": MODULES.split(","), "parser_version": importlib.metadata.version("opendataloader-pdf"),
                    "jar_sha256": hashlib.sha256((stage / "parser.jar").read_bytes()).hexdigest(),
                    "cds_sha256": hashlib.sha256((stage / "app-cds.jsa").read_bytes()).hexdigest(),
                    "jdk_source": "https://github.com/adoptium/temurin21-binaries/releases",
                    "release_note": "배포 시 사용한 JDK 빌드와 정확히 대응하는 소스 아카이브를 첨부해야 한다."}
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        tiny.unlink()
        shutil.rmtree(stage / "warmup")
        if output.exists():
            shutil.rmtree(output)
        stage.rename(output)
    print(output)


if __name__ == "__main__":
    main()
