"""Exercise real Soup CLI fixtures without network, GPU, or storage credentials."""

import json
import subprocess
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

IMAGE = "kitchensoup-soup-ingest:local"
image_id = subprocess.check_output(
    ["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"], text=True
).strip()

# Synthetic valid office/PDF envelopes, generated here rather than binary fixtures.
docx = BytesIO()
with ZipFile(docx, "w") as archive:
    archive.writestr(
        "[Content_Types].xml",
        """<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels"
 ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Override PartName="/word/document.xml"
 ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>""",
    )
    archive.writestr(
        "_rels/.rels",
        """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1"
 Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
 Target="word/document.xml"/></Relationships>""",
    )
    archive.writestr(
        "word/document.xml",
        """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t>Synthetic Word paragraph.</w:t></w:r></w:p></w:body></w:document>""",
    )
pdf = bytearray(b"%PDF-1.4\n")
stream = b"BT /F1 12 Tf 72 720 Td (Synthetic PDF page.) Tj ET"
objects = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
]
offsets = [0]
for index, obj in enumerate(objects, 1):
    offsets.append(len(pdf))
    pdf.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
xref = len(pdf)
pdf.extend(b"xref\n0 6\n0000000000 65535 f \n")
for offset in offsets[1:]:
    pdf.extend(f"{offset:010d} 00000 n \n".encode())
pdf.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())

fixtures = [
    (".txt", Path("tests/fixtures/documents/notes.txt").read_bytes(), 1),
    (".md", Path("tests/fixtures/documents/notes.md").read_bytes(), 2),
    (".docx", docx.getvalue(), 1),
    (".pdf", bytes(pdf), 1),
]
for extension, content, expected in fixtures + [(".pdf", b"%PDF-invalid fixture", -1)]:
    command = [
        "docker",
        "run",
        "--rm",
        "--interactive",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:size=268435456,mode=1777",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "1536m",
        "--cpus",
        "1",
        "--pids-limit",
        "64",
        "--env",
        "SOUP_IMAGE_ID=" + image_id,
        "--entrypoint",
        "python",
        image_id,
        "-c",
        "import sys,json; from runner import ingest; "
        "print(json.dumps(ingest(sys.stdin.buffer.read(), sys.argv[1])))",
        extension,
    ]
    outputs = []
    for _ in range(2 if expected >= 0 else 1):
        result = json.loads(subprocess.check_output(command, input=content, timeout=90))
        assert result["image_id"] == image_id and result["soup_version"] == "0.74.0"
        if expected < 0:
            assert result["exit_code"] != 0 and result["stderr"]
        else:
            assert result["exit_code"] == 0, result["stderr"]
            rows = [json.loads(line) for line in result["output"].splitlines()]
            assert len(rows) == expected and all("Synthetic" in row["text"] for row in rows)
            outputs.append(result["output"])
    if outputs:
        assert outputs[0] == outputs[1]
    outcome = "failure captured" if expected < 0 else "expected rows and byte-identical replay"
    print(f"Soup {extension}: {outcome}")
print("Real Soup fixture checks passed; image " + image_id)
