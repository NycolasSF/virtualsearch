# -*- coding: utf-8 -*-
"""OCR dos papers-imagem da CBSchool (PDFs sem camada de texto). Renderiza cada
pagina via PyMuPDF e roda Tesseract (lang=por), salvando <nome>.ocr.txt ao lado.
Idempotente (pula .ocr.txt valido). Roda em CPU (paralelo a transcricao na GPU).
"""
import os, glob, sys, time
import fitz
import pytesseract
from PIL import Image
import io
if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8",errors="replace")
fitz.TOOLS.mupdf_display_errors(False)

PDF_DIR = r"F:\claude-projetos\_acervo\library\cbschool\papers"
TESS_EXE = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSDATA = r"F:\claude-projetos\CLIENTES\marcio-medeiros-educacao\LANCAMENTOS\imersão-contabilidade\Material apresentação\_tessdata"
pytesseract.pytesseract.tesseract_cmd = TESS_EXE
os.environ["TESSDATA_PREFIX"] = TESSDATA
MIN_TEXT = 40  # PDF com menos texto que isso = imagem -> OCR

def has_text(path):
    try:
        t = "\n".join(p.get_text() for p in fitz.open(path)).strip()
        return len(t) >= MIN_TEXT
    except Exception:
        return False

def ocr_pdf(path):
    parts = []
    doc = fitz.open(path)
    for pg in doc:
        pix = pg.get_pixmap(dpi=200)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        parts.append(pytesseract.image_to_string(img, lang="por", config="--psm 6 --oem 3"))
    return "\n".join(parts).strip()

def main():
    pdfs = sorted(glob.glob(os.path.join(PDF_DIR, "*.pdf")))
    alvo = [f for f in pdfs if not has_text(f)]
    print(f"papers-imagem a OCR: {len(alvo)}")
    ok = 0
    for i, f in enumerate(alvo, 1):
        out = f[:-4] + ".ocr.txt"
        if os.path.exists(out) and os.path.getsize(out) > 100:
            print(f"[{i}/{len(alvo)}] -- ja OCR: {os.path.basename(f)}"); ok += 1; continue
        t0 = time.time()
        try:
            txt = ocr_pdf(f)
            if len(txt) < MIN_TEXT:
                print(f"[{i}/{len(alvo)}] !! OCR vazio: {os.path.basename(f)}"); continue
            open(out, "w", encoding="utf-8").write(txt)
            print(f"[{i}/{len(alvo)}] ok {len(txt)} chars em {time.time()-t0:.0f}s  {os.path.basename(f)[:45]}")
            ok += 1
        except Exception as e:
            print(f"[{i}/{len(alvo)}] ERRO {os.path.basename(f)} :: {e}")
    print(f"\n[fim] {ok}/{len(alvo)} papers-imagem com OCR (.ocr.txt)")

if __name__ == "__main__":
    main()
