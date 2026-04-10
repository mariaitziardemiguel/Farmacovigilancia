"""
Descarga PDFs de agendas PRAC → data/EMA PRAC/agendas/ (OPTIMIZADO CON PARALELO)
"""

import sys
import time
from pathlib import Path
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

BASE_URL  = "https://www.ema.europa.eu"
SLEEP     = 2.0
RETRY_WAITS = [30, 60, 120]
MAX_WORKERS = 4  # Número de descargas en paralelo

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

SEARCH_URL = (
    "https://www.ema.europa.eu/en/committees/"
    "pharmacovigilance-risk-assessment-committee-prac"
    "?doc_type_id_a%5B33443%5D=33443&doc_type_op_a=or"
    "&committee_id_a%5B100010%5D=100010&committee_op_a=and"
    "&categories_id_a%5B83%5D=83&categories_op_a=and"
    "&publishing_date_a%5Bmin%5D%5Bdate%5D="
    "&publishing_date_a%5Bmax%5D%5Bdate%5D="
)

OUTPUT = Path(__file__).parent.parent / "data" / "EMA PRAC" / "agendas"


def get_soup(url: str, session: requests.Session) -> tuple[BeautifulSoup, bool]:
    """Retorna (soup, hubo_429)"""
    hit_429 = False
    for attempt, wait in enumerate(RETRY_WAITS + [None], start=1):
        resp = session.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 429:
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser"), hit_429
        hit_429 = True
        if wait is None:
            resp.raise_for_status()
        print(f"  [429] Esperando {wait}s (intento {attempt})…")
        time.sleep(wait)
    raise RuntimeError("reintentos agotados")


def safe_filename(url: str) -> str:
    return url.rstrip("/").split("/")[-1].split("?")[0]


def download_pdf(url: str, dest: Path, session: requests.Session) -> tuple[str, bool]:
    """Descarga un PDF y retorna (filename, fue_descargado)"""
    filename = safe_filename(url)
    out_path = dest / filename
    if out_path.exists():
        return (filename, False)
    try:
        resp = session.get(url, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        out_path.write_bytes(resp.content)
        return (f"{filename} ({len(resp.content) / 1024:.0f} KB)", True)
    except requests.HTTPError as e:
        return (f"{filename} [ERROR: {e}]", False)


def process_page(page_num: int, session: requests.Session) -> tuple[int, int]:
    """Procesa una página: scrape + descarga de PDFs en paralelo"""
    url = f"{SEARCH_URL}&page=%2C{page_num}%2C0"
    try:
        soup, _ = get_soup(url, session)
    except requests.HTTPError as e:
        print(f"  [Página {page_num + 1}] HTTP error: {e}")
        return (0, 0)
    
    all_links = [
        urljoin(BASE_URL, a["href"])
        for a in soup.find_all("a", href=True)
        if a["href"].lower().endswith(".pdf")
    ]
    pdf_links = [u for u in all_links if safe_filename(u).startswith("agenda-")]
    
    if not pdf_links:
        return (0, 0)
    
    downloaded = skipped = 0
    print(f"--- Página {page_num + 1}: {len(pdf_links)} agendas")
    
    # Descargar PDFs de esta página en paralelo
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(download_pdf, purl, OUTPUT, session): purl
            for purl in pdf_links
        }
        for future in as_completed(futures):
            try:
                filename, was_downloaded = future.result()
                if was_downloaded:
                    print(f"    [✓] {filename}")
                    downloaded += 1
                else:
                    skipped += 1
            except Exception as e:
                print(f"    [✗] {e}")
    
    return (downloaded, skipped)


def check_and_download(session: requests.Session, start_page: int, max_pages: int | None) -> None:
    """Escanea todas las páginas rápido y descarga solo los PDFs que faltan."""
    sleep_cur = 1.0   # sleep inicial entre páginas
    sleep_min = 1.0   # mínimo al que puede bajar
    sleep_max = 8.0   # máximo tras varios 429s
    page_num = start_page
    max_attempts = max_pages or 200
    total_missing = total_downloaded = total_errors = 0

    print(f"[Modo check] Escaneando desde página {start_page + 1}...\n")

    for _ in range(max_attempts):
        url = f"{SEARCH_URL}&page=%2C{page_num}%2C0"
        try:
            time.sleep(sleep_cur)
            soup, hit_429 = get_soup(url, session)
            if hit_429:
                sleep_cur = min(sleep_cur * 2, sleep_max)
                print(f"  [rate limit] Subiendo sleep a {sleep_cur:.1f}s")
            else:
                sleep_cur = max(sleep_cur * 0.85, sleep_min)
        except requests.HTTPError as e:
            print(f"  [HTTP error p.{page_num + 1}] {e} — deteniendo.")
            break

        all_links = [
            urljoin(BASE_URL, a["href"])
            for a in soup.find_all("a", href=True)
            if a["href"].lower().endswith(".pdf")
        ]
        pdf_links = [u for u in all_links if safe_filename(u).startswith("agenda-")]
        if not pdf_links:
            print(f"  Página {page_num + 1}: sin agendas. Fin del listado.")
            break
        missing = [u for u in pdf_links if not (OUTPUT / safe_filename(u)).exists()]

        if missing:
            print(f"--- Página {page_num + 1}: {len(missing)}/{len(pdf_links)} agendas faltan → descargando...")
            total_missing += len(missing)
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(download_pdf, u, OUTPUT, session): u for u in missing}
                for future in as_completed(futures):
                    filename, was_downloaded = future.result()
                    if was_downloaded:
                        print(f"    [✓] {filename}")
                        total_downloaded += 1
                    else:
                        print(f"    [✗] {filename}")
                        total_errors += 1
        else:
            print(f"    Página {page_num + 1}: {len(pdf_links)} agendas — todas ya descargadas.")

        page_num += 1

    print(f"\n{'='*60}")
    print(f"Faltaban    : {total_missing}")
    print(f"Descargados : {total_downloaded}")
    print(f"Errores     : {total_errors}")
    print(f"Guardados en: {OUTPUT}")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    # Leer página de inicio desde argumentos (ej: python script.py 23 100)
    # Añadir --check para escanear rápido y descargar solo los que faltan
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    check_mode = "--check" in sys.argv

    start_page = int(args[0]) if len(args) > 0 else 0
    max_pages = int(args[1]) if len(args) > 1 else None

    if check_mode:
        check_and_download(session, start_page, max_pages)
        return
    
    print(f"Comenzando desde página {start_page + 1}...")
    print(f"Descargando hasta {max_pages or 'todas'} páginas de búsqueda...")
    
    # Primero: recopilar todas las URLs de búsqueda (páginas)
    page_urls = []
    page_num = start_page
    max_attempts = max_pages or 100
    
    print("\n[Fase 1] Recopilando URLs de búsqueda...")
    for attempt in range(max_attempts):
        url = f"{SEARCH_URL}&page=%2C{page_num}%2C0"
        try:
            time.sleep(SLEEP / 2)
            soup, _ = get_soup(url, session)
            all_links = [
                urljoin(BASE_URL, a["href"])
                for a in soup.find_all("a", href=True)
                if a["href"].lower().endswith(".pdf")
            ]
            pdf_count = len([u for u in all_links if safe_filename(u).startswith("agenda-")])
            if pdf_count == 0:
                print(f"  Página {page_num + 1}: Sin agendas. Fin del listado.")
                break
            print(f"  Página {page_num + 1}: {pdf_count} agendas encontradas")
            page_urls.append(page_num)
            page_num += 1
        except requests.HTTPError as e:
            print(f"  [HTTP error] {e} — deteniendo recopilación.")
            break
    
    print(f"\nEncontradas {len(page_urls)} páginas. Descargando PDFs en paralelo...\n")
    
    # Segundo: procesar cada página en paralelo
    downloaded = skipped = 0
    with ThreadPoolExecutor(max_workers=2) as page_executor:  # 2 páginas simultáneamente
        futures = {}
        for pnum in page_urls:
            future = page_executor.submit(process_page, pnum, session)
            futures[future] = pnum
        
        for future in as_completed(futures):
            pnum = futures[future]
            try:
                down, skip = future.result()
                downloaded += down
                skipped += skip
            except Exception as e:
                print(f"  [error página {pnum + 1}] {e}")

    print(f"\n{'='*60}")
    print(f"Descargados : {downloaded}")
    print(f"Ya existían : {skipped}")
    print(f"Guardados en: {OUTPUT}")


if __name__ == "__main__":
    main()
