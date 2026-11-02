"""Lightweight PDF page classification; text extraction belongs to execution."""
VERSION = 1


def classify(objects, width, height, has_text):
    area = max(width * height, 1)
    horizontal, vertical = [], []
    reasons = set()
    for kind, (left, bottom, right, top) in objects:
        w, h = abs(right - left), abs(top - bottom)
        margin = top <= height * .1 or bottom >= height * .9
        if kind == 'image':
            if not has_text or not (margin and w * h / area < .01):
                reasons.add('image')
        elif kind == 'path':
            if h <= 3 and w >= 12:
                horizontal.append((left, right, (bottom + top) / 2))
            elif w <= 3 and h >= 12:
                vertical.append(((left + right) / 2, bottom, top))
            elif w * h / area > .005 and not margin and not (has_text and w * h / area > .9):
                reasons.add('drawing')
    if len(horizontal) >= 2 and len(vertical) >= 2:
        intersections = 0
        for left, right, y in horizontal:
            for x, b, t in vertical:
                intersections += left - 2 <= x <= right + 2 and b - 2 <= y <= t + 2
                if intersections >= 4:
                    break
            if intersections >= 4:
                break
        if intersections >= 4:
            reasons.add('table')
    if not has_text and objects:
        reasons.add('scan_or_drawing')
    return {'vision': bool(reasons), 'reasons': sorted(reasons)}


def analyze(path):
    if path.suffix.lower() != '.pdf':
        from PIL import Image
        with Image.open(path) as image:
            count = getattr(image, 'n_frames', 1)
            if count > 1000:
                raise ValueError('이미지 프레임이 너무 많습니다.')
            return [{'vision': True, 'reasons': ['image']} for _ in range(count)]
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    pages = []
    with pdfium.PdfDocument(str(path)) as pdf:
        if len(pdf) > 1000:
            raise ValueError('한 파일은 1,000쪽 이하로 나누어 준비하세요.')
        for page in pdf:
            try:
                objects = []
                has_text = False
                for obj in page.get_objects():
                    if obj.type == raw.FPDF_PAGEOBJ_TEXT:
                        has_text = True
                    elif obj.type in (raw.FPDF_PAGEOBJ_IMAGE, raw.FPDF_PAGEOBJ_PATH):
                        objects.append(('image' if obj.type == raw.FPDF_PAGEOBJ_IMAGE else 'path', obj.get_bounds()))
                    if len(objects) > 10000:
                        break
                pages.append({'vision': True, 'reasons': ['complex_page']} if len(objects) > 10000
                             else classify(objects, *page.get_size(), has_text))
            finally:
                page.close()
    return pages


def extract_text(path, index):
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(str(path)) as pdf:
        page = pdf[index]
        try:
            textpage = page.get_textpage()
            try:
                return textpage.get_text_bounded()
            finally:
                textpage.close()
        finally:
            page.close()
