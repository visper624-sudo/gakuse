"""多格式文档解析：TXT / Markdown / DOCX / PDF → 结构化段落块（含页码、段落号、字符区间）。

零第三方依赖实现：
  * DOCX 直接解压 word/document.xml 读取段落与标题样式；
  * PDF 自带解析器：解析对象表 → 页树 → 字体 ToUnicode CMap → 内容流文本算子，
    可正确抽取 Word 等工具导出、带 ToUnicode 映射的中文 PDF；
  * 如环境内安装了 pypdf/pdfplumber，则在设置中可切换为增强后端。
"""
from __future__ import annotations

import re
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ParseError(RuntimeError):
    pass


@dataclass
class Block:
    text: str
    heading_path: str = ""
    page_no: int = 1
    para_index: int = 0
    char_start: int = 0
    char_end: int = 0
    level: int = 0          # 0=正文，1..6=标题层级


@dataclass
class ParsedDoc:
    blocks: list[Block] = field(default_factory=list)
    format: str = "txt"
    page_count: int = 1
    text_length: int = 0

    @property
    def full_text(self) -> str:
        return "".join(b.text for b in self.blocks)


def _finalize(blocks: Iterable[Block]) -> ParsedDoc:
    out: list[Block] = []
    pos = 0
    for i, b in enumerate(blocks):
        text = (b.text or "").strip("\n")
        if not text.strip():
            continue
        if not text.endswith("\n"):
            text += "\n"
        b.text = text
        b.char_start = pos
        pos += len(text)
        b.char_end = pos
        b.para_index = len(out)
        out.append(b)
    doc = ParsedDoc(blocks=out, text_length=pos)
    doc.page_count = max((b.page_no for b in out), default=1)
    return doc


# ---------------------------------------------------------------- Markdown / TXT
MD_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")
CN_HEAD = re.compile(r"^(第[一二三四五六七八九十百零\d]+[章节部分篇]|[一二三四五六七八九十]+、|\d+[.、]\s*[^\d\s])")


def parse_text(text: str, fmt: str = "txt") -> ParsedDoc:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[Block] = []
    stack: list[tuple[int, str]] = []
    page = 1
    chars_in_page = 0
    buf: list[str] = []

    def flush():
        nonlocal buf
        if buf:
            body = " ".join(x.strip() for x in buf if x.strip())
            if body:
                blocks.append(Block(
                    text=body,
                    heading_path=" > ".join(t for _, t in stack),
                    page_no=page,
                ))
            buf = []

    for line in lines:
        chars_in_page += len(line)
        if chars_in_page > 1400:
            page += 1
            chars_in_page = 0

        m = MD_HEAD.match(line) if fmt == "md" else None
        if m:
            flush()
            level, title = len(m.group(1)), m.group(2).strip()
            stack = [s for s in stack if s[0] < level] + [(level, title)]
            blocks.append(Block(
                text=title + "\n",
                heading_path=" > ".join(t for _, t in stack),
                page_no=page,
                level=level,
            ))
            continue

        if not line.strip():
            flush()
            continue

        if fmt == "txt":
            cm = CN_HEAD.match(line.strip())
            if cm and len(line.strip()) <= 40:
                flush()
                blocks.append(Block(
                    text=line.strip() + "\n",
                    heading_path=" > ".join(t for _, t in stack) + (" > " if stack else "") + line.strip(),
                    page_no=page,
                    level=1,
                ))
                stack = [(1, line.strip())]
                continue

        buf.append(line)
    flush()
    doc = _finalize(blocks)
    doc.format = fmt
    return doc


# ---------------------------------------------------------------- DOCX
def parse_docx(path: Path) -> ParsedDoc:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml")
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"DOCX 解析失败：{exc}") from exc
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml)
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"DOCX 结构损坏：{exc}") from exc

    body = root.find(f"{W_NS}body")
    blocks: list[Block] = []
    stack: list[tuple[int, str]] = []
    page = 1
    chars = 0

    def para_text(p) -> str:
        return "".join(t.text or "" for t in p.iter(f"{W_NS}t"))

    def para_style(p) -> str:
        ppr = p.find(f"{W_NS}pPr")
        if ppr is None:
            return ""
        st = ppr.find(f"{W_NS}pStyle")
        return st.get(f"{W_NS}val", "") if st is not None else ""

    for el in (body if body is not None else []):
        if el.tag == f"{W_NS}p":
            text = para_text(el).strip()
            if not text:
                continue
            style = para_style(el).lower()
            level = 0
            if "heading" in style:
                m = re.search(r"(\d+)", style)
                level = int(m.group(1)) if m else 1
            elif re.match(r"^标题\s*\d", style) or style.startswith("2") and len(style) <= 3:
                level = 1
            chars += len(text)
            if chars > 1200:
                page += 1
                chars = 0
            if level:
                stack = [s for s in stack if s[0] < level] + [(level, text)]
                blocks.append(Block(text=text + "\n", heading_path=" > ".join(t for _, t in stack),
                                    page_no=page, level=level))
            else:
                blocks.append(Block(text=text + "\n", heading_path=" > ".join(t for _, t in stack),
                                    page_no=page))
        elif el.tag == f"{W_NS}tbl":
            for row in el.iter(f"{W_NS}tr"):
                cells = []
                for cell in row.findall(f"{W_NS}tc"):
                    cell_text = " ".join(para_text(p).strip() for p in cell.iter(f"{W_NS}p")).strip()
                    if cell_text:
                        cells.append(cell_text)
                if cells:
                    blocks.append(Block(text=" | ".join(cells) + "\n",
                                        heading_path=" > ".join(t for _, t in stack), page_no=page))
    doc = _finalize(blocks)
    doc.format = "docx"
    return doc


# ---------------------------------------------------------------- PDF（内置解析器）
_OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj\b")


class _Ref:
    __slots__ = ("num",)

    def __init__(self, num: int):
        self.num = num


class PdfReader:
    """极简 PDF 文本抽取器（支持 FlateDecode + ToUnicode / Identity-H）。"""

    def __init__(self, data: bytes):
        self.data = data
        self.objects: dict[int, tuple[bytes, bytes | None]] = {}
        self._scan_objects()

    # ---- 对象表
    def _scan_objects(self) -> None:
        """不依赖 xref：按对象头逐段扫描，正确处理字典嵌套与流边界。"""
        data = self.data
        pos = 0
        obj_stms: list[int] = []
        while True:
            m = _OBJ_RE.search(data, pos)
            if not m:
                break
            num = int(m.group(1))
            start = m.end()
            dict_end, stream_start, stream_end = self._object_bounds(data, start)
            dict_part = data[start:dict_end]
            raw = data[stream_start:stream_end] if stream_start else None
            if raw is not None:
                raw = raw.rstrip(b"\r\n")
            self.objects[num] = (dict_part, raw)
            if re.search(rb"/Type\s*/ObjStm", dict_part):
                obj_stms.append(num)
            pos = (stream_end + 9) if stream_start else dict_end
            pos = max(pos, start + 1)
        # 展开对象流（PDF 1.5 压缩对象）
        for num in obj_stms:
            try:
                self._expand_objstm(num)
            except Exception:  # noqa: BLE001
                continue

    @staticmethod
    def _object_bounds(data: bytes, start: int) -> tuple[int, int, int]:
        i = start
        n = len(data)
        depth = 0
        while i < n:
            c = data[i]
            if c == 0x25:                       # 注释
                nl = data.find(b"\n", i)
                i = n if nl < 0 else nl + 1
                continue
            if c == 0x28:                       # 字面字符串
                i += 1
                while i < n:
                    if data[i] == 0x5C:
                        i += 2
                        continue
                    if data[i] == 0x29:
                        i += 1
                        break
                    i += 1
                continue
            if c == 0x3C and i + 1 < n and data[i + 1] == 0x3C:
                depth += 1
                i += 2
                continue
            if c == 0x3E and i + 1 < n and data[i + 1] == 0x3E:
                depth -= 1
                i += 2
                continue
            if c in (0x5B, 0x5D):
                depth += 1 if c == 0x5B else -1
                i += 1
                continue
            if depth <= 0 and data[i:i + 6] == b"stream":
                s = i + 6
                if data[s:s + 2] == b"\r\n":
                    s += 2
                elif data[s:s + 1] in (b"\n", b"\r"):
                    s += 1
                e = data.find(b"endstream", s)
                if e < 0:
                    e = n
                return i, s, e
            if depth <= 0 and data[i:i + 6] == b"endobj":
                return i, 0, 0
            i += 1
        return n, 0, 0

    def _expand_objstm(self, num: int) -> None:
        dict_part, _ = self.objects.get(num, (b"", None))
        m_n = re.search(rb"/N\s+(\d+)", dict_part)
        m_first = re.search(rb"/First\s+(\d+)", dict_part)
        if not m_n or not m_first:
            return
        count, first = int(m_n.group(1)), int(m_first.group(1))
        body = self.stream_bytes(num)
        header, payload = body[:first], body[first:]
        pairs = re.findall(rb"(\d+)\s+(\d+)", header)
        offsets = [(int(a), int(b)) for a, b in pairs][:count]
        for idx, (onum, off) in enumerate(offsets):
            end = offsets[idx + 1][1] if idx + 1 < len(offsets) else len(payload)
            chunk = payload[off:end]
            if onum in self.objects:
                continue
            self.objects[onum] = (chunk, None)

    def stream_bytes(self, num: int) -> bytes:
        dict_part, raw = self.objects.get(num, (b"", None))
        if raw is None:
            return b""
        if b"FlateDecode" in dict_part:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                try:
                    return zlib.decompressobj().decompress(raw)
                except Exception:  # noqa: BLE001
                    return b""
        return raw

    # ---- 基础语法
    @staticmethod
    def _dict_of(body: bytes, key: bytes) -> bytes | None:
        m = re.search(rb"/" + key + rb"\s*(\[[^\]]*\]|<<.*?>>|\d+\s+\d+\s+R|/[^\s/<>\[\]()]+|<[0-9A-Fa-f\s]*>|\d+(?:\.\d+)?)", body, re.S)
        return m.group(1) if m else None

    def _ref_of(self, token: bytes | None) -> int | None:
        if not token:
            return None
        m = re.match(rb"(\d+)\s+\d+\s+R", token.strip())
        return int(m.group(1)) if m else None

    def pages(self) -> list[int]:
        out = []
        for num, (dict_part, _raw) in self.objects.items():
            if b"/Type" in dict_part and re.search(rb"/Type\s*/Page\b", dict_part) and b"/Kids" not in dict_part:
                out.append(num)
        return sorted(out)

    # ---- 字体 / CMap
    def _to_unicode(self, font_ref: int | None) -> dict[int, str]:
        if not font_ref or font_ref not in self.objects:
            return {}
        dict_part, _ = self.objects[font_ref]
        m = re.search(rb"/ToUnicode\s+(\d+)\s+\d+\s+R", dict_part)
        if not m:
            return {}
        cmap = self._parse_cmap(self.stream_bytes(int(m.group(1))))
        return cmap

    @staticmethod
    def _hex_to_bytes(hexstr: bytes) -> bytes:
        s = re.sub(rb"[^0-9A-Fa-f]", b"", hexstr)
        if len(s) % 2:
            s += b"0"
        return bytes.fromhex(s.decode())

    def _parse_cmap(self, data: bytes) -> dict[int, str]:
        mapping: dict[int, str] = {}
        for block in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
            for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
                code = int(src, 16)
                raw = self._hex_to_bytes(dst)
                mapping[code] = raw.decode("utf-16-be", "ignore")
        for block in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
            for m in re.finditer(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])", block):
                lo, hi, dst = int(m.group(1), 16), int(m.group(2), 16), m.group(3)
                if dst.startswith(b"["):
                    items = re.findall(rb"<([0-9A-Fa-f]+)>", dst)
                    for i, item in enumerate(items):
                        mapping[lo + i] = self._hex_to_bytes(item).decode("utf-16-be", "ignore")
                else:
                    base = self._hex_to_bytes(dst[1:-1])
                    base_code = int.from_bytes(base, "big")
                    for i in range(0, min(hi - lo + 1, 65536)):
                        try:
                            mapping[lo + i] = (base_code + i).to_bytes(2, "big").decode("utf-16-be", "ignore")
                        except Exception:  # noqa: BLE001
                            break
        return mapping

    # ---- 内容流
    def _page_fonts(self, page_obj: int) -> dict[str, tuple[int | None, bool]]:
        dict_part, _ = self.objects[page_obj]
        resources = dict_part
        rm = re.search(rb"/Resources\s+(\d+)\s+\d+\s+R", dict_part)
        if rm:
            resources = self.objects.get(int(rm.group(1)), (b"", None))[0]
        fm = re.search(rb"/Font\s*<<(.*?)>>", resources, re.S)
        if not fm:
            fr = re.search(rb"/Font\s+(\d+)\s+\d+\s+R", resources)
            if fr:
                fdict = self.objects.get(int(fr.group(1)), (b"", None))[0]
            else:
                return {}
        else:
            fdict = fm.group(1)
        fonts: dict[str, tuple[int | None, bool]] = {}
        for m in re.finditer(rb"/([A-Za-z0-9#_.+-]+)\s+(\d+)\s+\d+\s+R", fdict):
            name, ref = m.group(1).decode("latin-1"), int(m.group(2))
            fbody = self.objects.get(ref, (b"", None))[0]
            two_byte = bool(re.search(rb"/Subtype\s*/Type0", fbody))
            fonts[name] = (ref, two_byte)
        return fonts

    def _page_content(self, page_obj: int) -> bytes:
        dict_part, _ = self.objects[page_obj]
        m = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", dict_part)
        if m:
            return self.stream_bytes(int(m.group(1)))
        arr = re.search(rb"/Contents\s*\[(.*?)\]", dict_part, re.S)
        if arr:
            out = b""
            for n in re.findall(rb"(\d+)\s+\d+\s+R", arr.group(1)):
                out += self.stream_bytes(int(n)) + b"\n"
            return out
        return b""

    @staticmethod
    def _decode_literal(raw: bytes) -> bytes:
        out = bytearray()
        i = 0
        while i < len(raw):
            c = raw[i]
            if c == 0x5C and i + 1 < len(raw):
                nxt = raw[i + 1]
                mp = {0x6E: b"\n", 0x72: b"\r", 0x74: b"\t", 0x62: b"\b", 0x66: b"\f",
                      0x28: b"(", 0x29: b")", 0x5C: b"\\"}
                if nxt in mp:
                    out += mp[nxt]
                    i += 2
                    continue
                if 0x30 <= nxt <= 0x37:
                    oct_digits = raw[i + 1:i + 4]
                    digits = b""
                    for ch in oct_digits:
                        if 0x30 <= ch <= 0x37:
                            digits += bytes([ch])
                        else:
                            break
                    out.append(int(digits, 8) & 0xFF)
                    i += 1 + len(digits)
                    continue
                out += bytes([nxt])
                i += 2
                continue
            out.append(c)
            i += 1
        return bytes(out)

    def extract_page_text(self, page_obj: int) -> str:
        content = self._page_content(page_obj)
        if not content:
            return ""
        fonts = self._page_fonts(page_obj)
        cmaps: dict[str, dict[int, str]] = {}
        two_byte: dict[str, bool] = {}
        for name, (ref, tb) in fonts.items():
            cmaps[name] = self._to_unicode(ref)
            two_byte[name] = tb

        cur_font = ""
        pieces: list[str] = []
        last_y = None
        pending_break = "\n"

        def decode(raw: bytes, font: str) -> str:
            cmap = cmaps.get(font, {})
            tb = two_byte.get(font, True)
            if tb:
                codes = [int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw) - 1, 2)]
            else:
                codes = list(raw)
            if cmap:
                return "".join(cmap.get(c, "") for c in codes)
            if tb:
                try:
                    return raw.decode("utf-16-be", "ignore")
                except Exception:  # noqa: BLE001
                    return ""
            return raw.decode("latin-1", "ignore")

        token_re = re.compile(
            rb"/([A-Za-z0-9#_.+-]+)\s+[\d.]+\s+Tf|"
            rb"(\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]+>)\s*Tj|"
            rb"\[((?:\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]+>|[^\]])*)\]\s*TJ|"
            rb"(?:([-\d.]+)\s+([-\d.]+)\s+(?:Td|TD))|"
            rb"(T\*)|"
            rb"(BT)|(ET)|"
            rb"(?:([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+Tm)",
            re.S,
        )
        for m in token_re.finditer(content):
            if m.group(1):                     # Tf
                cur_font = m.group(1).decode("latin-1")
            elif m.group(2):                   # Tj
                raw = m.group(2)
                if raw.startswith(b"("):
                    data = self._decode_literal(raw[1:-1])
                else:
                    data = self._hex_to_bytes(raw[1:-1])
                s = decode(data, cur_font)
                if s:
                    pieces.append(s)
            elif m.group(3) is not None:       # TJ
                for item in re.finditer(rb"\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]+>|([-\d.]+)", m.group(3)):
                    seg = item.group(0)
                    if seg in (b"") or (seg and seg[0:1] == b"-") or re.fullmatch(rb"[-\d.]+", seg):
                        try:
                            if float(seg) < -180:
                                pieces.append(" ")
                        except ValueError:
                            pass
                        continue
                    if seg.startswith(b"("):
                        data = self._decode_literal(seg[1:-1])
                    else:
                        data = self._hex_to_bytes(seg[1:-1])
                    s = decode(data, cur_font)
                    if s:
                        pieces.append(s)
            elif m.group(4) is not None:       # Td
                dy = float(m.group(5) or 0)
                if abs(dy) > 2:
                    pieces.append("\n")
            elif m.group(6):                   # T*
                pieces.append("\n")
            elif m.group(7):                   # BT
                if pieces and not pieces[-1].endswith("\n"):
                    pieces.append("\n")
            elif m.group(9) is not None:       # Tm
                pieces.append("\n")

        text = "".join(pieces)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def parse_pdf(path: Path) -> ParsedDoc:
    reader = PdfReader(path.read_bytes())
    page_objs = reader.pages()
    if not page_objs:
        raise ParseError("PDF 未解析到页面对象（可能为扫描件或加密文档）")
    blocks: list[Block] = []
    total_chars = 0
    head_re = re.compile(r"^(第[一二三四五六七八九十百零\d]+[章节部分篇]|[一二三四五六七八九十]+、|\d+\.\s*[^\d])")
    for idx, pobj in enumerate(page_objs, start=1):
        text = reader.extract_page_text(pobj)
        if not text.strip():
            continue
        total_chars += len(text)
        # 按语义合并折行：句末标点或标题行才断段
        paras: list[str] = []
        buf = ""
        for line in (x.strip() for x in text.split("\n")):
            if not line:
                continue
            if head_re.match(line) and len(line) <= 40:
                if buf:
                    paras.append(buf)
                    buf = ""
                paras.append(line)
                continue
            buf += line
            if line[-1] in "。！？；：!?;:":
                paras.append(buf)
                buf = ""
        if buf:
            paras.append(buf)
        for para in paras:
            if para.strip():
                blocks.append(Block(text=para + "\n", heading_path="", page_no=idx))
    if total_chars < 20:
        raise ParseError("PDF 文本层为空（可能为扫描件），需接入 OCR 适配器后再试")
    doc = _finalize(blocks)
    doc.format = "pdf"
    return doc


# ---------------------------------------------------------------- 分发
def parse_file(path: Path) -> ParsedDoc:
    suffix = path.suffix.lower().lstrip(".")
    if suffix in ("md", "markdown"):
        return parse_text(path.read_text(encoding="utf-8", errors="ignore"), "md")
    if suffix in ("txt", "text"):
        return parse_text(path.read_text(encoding="utf-8", errors="ignore"), "txt")
    if suffix == "docx":
        return parse_docx(path)
    if suffix == "pdf":
        return parse_pdf(path)
    if suffix == "doc":
        raise ParseError("暂不支持 .doc 二进制格式，请另存为 .docx 后导入")
    raise ParseError(f"不支持的文件格式：.{suffix}")
