# -*- coding: utf-8 -*-
"""
Hlídač inzerátů aut: Bazoš.cz, Sauto.cz, Sbazar.cz, Autobazar.eu, mobile.de a Otomoto.pl.

Pro každé auto v AUTA vytvoří:
  docs/<id>.html      report pro to auto (složka docs = web na GitHub Pages)
  state/<id>.json     paměť (co už bylo vidět, za kolik, stažené detaily)
a k tomu docs/index.html s přehledem všech aut. Když jsou nastavené proměnné
SMTP_USER, SMTP_HESLO a EMAIL_KOMU, pošle e-mail s nejlepšími inzeráty.

Spuštění:
  python hlidac_aut.py                 všechna auta
  python hlidac_aut.py rapid-mc        jen vybraná auta (id oddělená mezerou)
  python hlidac_aut.py --debug         podrobný výpis pro ladění
  python hlidac_aut.py --bez-mailu     neposílat e-mail
"""
import html
import json
import os
import re
import smtplib
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ================= NASTAVENÍ =================
KURZY = {"CZK": 1.0, "EUR": 25.0, "PLN": 5.9}   # záložní kurzy, při běhu se načtou aktuální z ČNB
MAIL_TOP = 10          # kolik inzerátů od každého auta dát do e-mailu
DNY_PAMATOVAT = 30     # jak dlouho si pamatovat inzerát, který zmizel
PAUZA = 0.7            # sekundy mezi dotazy na web
MAX_DETAILU = 80       # kolik nových detailů inzerátů max. stáhnout za běh u jednoho auta
CENA_MIN = 40000       # levnější inzeráty jsou skoro vždy díly, ignorují se

# Klíče u auta:
#   id                 krátký název bez mezer, podle něj se jmenují soubory
#   nazev              nadpis reportu
#   hledat             fulltext pro Bazoš a Sbazar
#   bazos_kategorie    značka na Bazoši podle adresy auto.bazos.cz/skoda/  ->  "skoda" (omezí díly z jiných značek)
#   sauto_model        z adresy sauto.cz/inzerce/osobni/skoda/rapid  ->  "skoda:rapid"
#   sauto_extra        další parametry Sauto API, např. {"vehicle_body_seo": "kupe"}
#   sbazar_kategorie   kategorie značky na Sbazaru: Škoda 152, VW 155, Audi 157, BMW 159 (bez ní se Sbazar přeskočí)
#   autobazar_model    z adresy autobazar.eu/vysledky/osobne-vozidla/skoda/rapid/  ->  "skoda/rapid"
#   mobile_url         celá adresa hledání z suchen.mobile.de (servery GitHubu mobile.de blokuje, funguje jen z domu)
#   otomoto_url        celá adresa hledání z otomoto.pl (stejně tak)
#   musi_obsahovat     stačí jedno slovo, hledá se v názvu i popisu
#   musi_v_nazvu       nepovinné, aspoň jedno z těchto slov musí být přímo v názvu (např. řada modelu)
#   nesmi_obsahovat    hledá se jen v názvu
#   kw                 seznam povolených výkonů, None = nekontrolovat
#   rok_od, cena_do    cena v Kč
#   jen_manual         True = vyřadit inzeráty, kde je uvedený automat
#   karoserie          např. ["kupe", "coupe"]; když ji zdroj uvádí, musí sedět (a nahradí klíčové slovo)
# Klíč zdroje vynech nebo dej None, když ho pro dané auto nechceš.
MOBILE = "https://suchen.mobile.de/fahrzeuge/search.html?dam=false&isSearchRequest=true&s=Car&vc=Car&sb=p&od=up"
OTOMOTO = "&search%5Bfilter_enum_gearbox%5D=manual&search%5Bfilter_float_price%3Ato%5D=34000&search%5Border%5D=filter_float_price%3Aasc"
DILY = ["dily", "dil", "nahradni", "rozprodam", "vrakovisko", "havarovane"]
AUTA = [
    {
        "id": "rapid-mc",
        "nazev": "Škoda Rapid Monte Carlo 81 kW, 2015+",
        "hledat": "rapid monte carlo",
        "bazos_kategorie": "skoda",
        "sauto_model": "skoda:rapid",
        "sauto_extra": {"phrase": "monte carlo"},
        "sbazar_kategorie": 152,
        "autobazar_model": "skoda/rapid",
        "musi_obsahovat": ["monte carlo", "montecarlo"],
        "nesmi_obsahovat": ["1.4", "1,4"] + DILY,
        "kw": [81],
        "rok_od": 2015,
        "cena_do": 350000,
    },
    {
        "id": "scala",
        "nazev": "Škoda Scala, do 350 000 Kč",
        "hledat": "scala",
        "bazos_kategorie": "skoda",
        "sauto_model": "skoda:scala",
        "sbazar_kategorie": 152,
        "autobazar_model": "skoda/scala",
        "musi_obsahovat": ["scala"],
        "nesmi_obsahovat": DILY,
        "rok_od": 2019,
        "cena_do": 350000,
    },
    {
        "id": "fabia",
        "nazev": "Škoda Fabia 2015+, do 350 000 Kč",
        "hledat": "fabia",
        "bazos_kategorie": "skoda",
        "sauto_model": "skoda:fabia",
        "sbazar_kategorie": 152,
        "autobazar_model": "skoda/fabia",
        "musi_obsahovat": ["fabia"],
        "nesmi_obsahovat": DILY,
        "rok_od": 2015,
        "cena_do": 350000,
    },
    {
        "id": "bmw-1-coupe",
        "nazev": "BMW 1 Coupé (E82), manuál, do 200 000 Kč",
        "hledat": "coupe",
        "bazos_kategorie": "bmw",
        "sauto_model": "bmw:rada-1",
        "sauto_extra": {"vehicle_body_seo": "kupe"},
        "sbazar_kategorie": 159,
        "autobazar_model": "bmw/rad-1",
        "otomoto_url": ("https://www.otomoto.pl/osobowe/bmw/seria-1/od-2007?search%5Bfilter_enum_body_type%5D=coupe"
                        + OTOMOTO),
        "musi_obsahovat": ["coupe", "kupe", "e82"],
        "musi_v_nazvu": ["e82", "114", "116", "118", "120", "123", "125", "128", "135",
                         "rada 1", "rad 1", "seria 1", "1er", "1 series"],
        "nesmi_obsahovat": ["cabrio", "kabrio", "kabriolet", "e88", "e92", "e90", "rada 3", "rad 3", "seria 3",
                            "3er"] + DILY,
        "rok_od": 2007,
        "cena_do": 200000,
        "jen_manual": True,
        "karoserie": ["kupe", "coupe"],
    },
    {
        "id": "bmw-3-coupe",
        "nazev": "BMW 3 Coupé (E92), manuál, do 200 000 Kč",
        "hledat": "coupe",
        "bazos_kategorie": "bmw",
        "sauto_model": "bmw:rada-3",
        "sauto_extra": {"vehicle_body_seo": "kupe"},
        "sbazar_kategorie": 159,
        "autobazar_model": "bmw/rad-3",
        "otomoto_url": ("https://www.otomoto.pl/osobowe/bmw/seria-3/od-2006?search%5Bfilter_enum_body_type%5D=coupe"
                        + OTOMOTO),
        "musi_obsahovat": ["coupe", "kupe", "e92"],
        "musi_v_nazvu": ["e92", "316", "318", "320", "323", "325", "328", "330", "335",
                         "rada 3", "rad 3", "seria 3", "3er", "3 series"],
        "nesmi_obsahovat": ["cabrio", "kabrio", "kabriolet", "e93", "e46", "e36", "e90", "e91", "touring",
                            "compact", "e82", "e87", "rada 1", "rad 1", "seria 1", "1er"] + DILY,
        "rok_od": 2006,
        "cena_do": 200000,
        "jen_manual": True,
        "karoserie": ["kupe", "coupe"],
    },
    {
        "id": "golf-7",
        "nazev": "VW Golf 7 hatchback, manuál, do 200 000 Kč",
        "hledat": "golf",
        "bazos_kategorie": "volkswagen",
        "sauto_model": "volkswagen:golf",
        "sauto_extra": {"vehicle_body_seo": "hatchback"},
        "sbazar_kategorie": 155,
        "autobazar_model": "volkswagen/golf",
        "otomoto_url": ("https://www.otomoto.pl/osobowe/volkswagen/golf/od-2013?search%5Bfilter_enum_body_type%5D=compact"
                        + OTOMOTO),
        "musi_obsahovat": ["golf"],
        "nesmi_obsahovat": ["variant", "kombi", "combi", "sportsvan", "golf plus", "cabrio", "golf 8", "golf viii"] + DILY,
        "rok_od": 2013,
        "cena_do": 200000,
        "jen_manual": True,
        "karoserie": ["hatchback", "kompakt", "compact"],
    },
    {
        "id": "audi-tt-8j",
        "nazev": "Audi TT 8J Coupé, manuál, do 200 000 Kč",
        "hledat": "tt",
        "bazos_kategorie": "audi",
        "sauto_model": "audi:tt",
        "sbazar_kategorie": 157,
        "autobazar_model": "audi/tt-coupe",
        "otomoto_url": ("https://www.otomoto.pl/osobowe/audi/tt/od-2006?search%5Bfilter_enum_body_type%5D=coupe"
                        + OTOMOTO),
        "musi_obsahovat": ["tt"],
        "nesmi_obsahovat": ["roadster", "cabrio", "kabrio", "kabriolet"] + DILY,
        "rok_od": 2006,
        "cena_do": 200000,
        "jen_manual": True,
    },
]
# =============================================

ZAKLAD = Path(__file__).resolve().parent
SLOZKA_REPORTY = ZAKLAD / "docs"
SLOZKA_STAV = ZAKLAD / "state"
DEBUG = "--debug" in sys.argv
BEZ_MAILU = "--bez-mailu" in sys.argv
MENY = {"CZK": "Kč", "EUR": "€", "PLN": "zł"}
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
})


def log(*a):
    if DEBUG:
        print(*a)


def get(url, **kw):
    r = SESSION.get(url, timeout=30, **kw)
    r.raise_for_status()
    return r


def norm(t):
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t.lower()).strip()


def obsahuje(text, slovo):
    """Celé slovo. '1.4' najde i v '1.4tsi', ale ne v '11.4' ani '1.45'."""
    s = norm(slovo)
    if not s:
        return False
    if len(s) >= 6 and not s[0].isdigit():
        return s in text              # dlouhé slovo stačí jako podřetězec ("1.0tsi81kwmontecarlo")
    vlevo = r"(?<![a-z0-9.,])" if s[0].isdigit() else r"(?<![a-z0-9])"
    # číslo nesmí pokračovat číslicí ani jednotkou: "120" sedí na "120d", ne na "120 kw" nebo "120 000 km"
    vpravo = r"(?!\d|[.,]\d|\s?(?:kw|ps|hp|nm|km|tis|xxx|\d{3}))" if s[-1].isdigit() else r"(?![a-z0-9])"
    return re.search(vlevo + re.escape(s) + vpravo, text) is not None


def text_z(el):
    return el.get_text(" ", strip=True) if el else ""


def cislo(n):
    return f"{n:,}".replace(",", " ")


def ted():
    """Pražský čas i na serveru GitHubu, který běží v UTC."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Prague"))
    except Exception:
        return datetime.now()


def nacti_kurzy():
    try:
        t = get("https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/"
                "kurzy-devizoveho-trhu/denni_kurz.txt").text
        for radek in t.splitlines():
            c = radek.split("|")
            if len(c) == 5 and c[3] in KURZY:
                KURZY[c[3]] = float(c[4].replace(",", ".")) / float(c[2])
        log(f"Kurzy ČNB: {KURZY}")
    except Exception as e:
        print(f"Kurzy z ČNB se nenačetly ({e}), používám záložní {KURZY}")


# ---------- vytahování údajů z textu ----------
ROK = r"(19[89]\d|20[0-3]\d)"
NUM = r"(?<!\d)(\d{1,3}(?:[ .]\d{3})+|\d{1,7})"


def cena_z_textu(t):
    cislice = re.sub(r"\D", "", t or "")
    if not cislice:
        return None, "CZK"
    return int(cislice), ("EUR" if ("€" in t or "eur" in t.lower()) else "CZK")


def najdi_rok(nazev, popis=""):
    m = re.search(r"(?<![\d.,])" + ROK + r"(?![\d.,]\d)", norm(nazev))
    if m:
        return int(m.group(1))
    m = re.search(r"(?:r\.? ?v\.?|rok a mesic vyroby|rok vyroby|vyroba|vyrobeno|1\. ?registrace|registrace|"
                  r"v provozu od|rocnik|rok)\s*:?\s*(?:\d{1,2}\s*[./]\s*){0,2}" + ROK + r"(?!\d)", norm(popis))
    return int(m.group(1)) if m else None


def najdi_kw(nazev, popis=""):
    for t in (nazev, popis):
        for m in re.finditer(r"(?<![\d.,])(\d{2,3})\s*kw", norm(t)):
            v = int(m.group(1))
            if 30 <= v <= 450:
                return v
    return None


def _km(cislo_txt, jednotka):
    v = int(re.sub(r"\D", "", cislo_txt))
    if jednotka and not jednotka.startswith("km") and v < 1000:
        v *= 1000                     # 134 tis. km, 134 tkm, 134 xxx km
    return v if 1000 <= v <= 600000 else None


def najdi_km(text):
    t = norm(text)
    # 1) s popiskem: "Nájezd: 134 XXX km", "najeto 134 tis.", "Stav tachometru: 107500 km"
    for m in re.finditer(r"(?:najezd|najeto|najezdeno|stav tachometru|tachometr|tacho)\s*:?\s*(?:cca\.?\s*)?"
                         + NUM + r"\s*(xxx|tis\.?|tkm|km)?", t):
        v = _km(m.group(1), m.group(2))
        if v:
            return v
    # 2) bez popisku vezmi největší rozumné číslo s km (ať to není "výměna oleje každých 15 000 km")
    kandidati = []
    for m in re.finditer(NUM + r"\s*(xxx\s*km|tis\.? ?km|tkm|km)(?![a-z])", t):
        v = _km(m.group(1), m.group(2))
        if v:
            kandidati.append(v)
    return max(kandidati) if kandidati else None


def najdi_prevodovku(nazev, popis=""):
    m = re.search(r"prevodovka\s*:?\s*(\d\s*-?\s*st\.?\s*)?(manual|automat|dsg)", norm(popis))
    if m:
        return "manual" if m.group(2) == "manual" else "automat"
    n = norm(nazev)
    if re.search(r"(?<![a-z])(automat|dsg|s ?tronic|steptronic|tiptronic|multitronic|cvt)(?![a-z])", n):
        return "automat"
    if re.search(r"(?<![a-z])(manual|manualni|manualem)(?![a-z])", n):
        return "manual"
    p = norm(popis)
    if (re.search(r"(?<![a-z])(manual|manualni|manualem|manualni prevodovk\w*)(?![a-z])", p)
            and not re.search(r"(?<![a-z])(automat|automaticka prevodovka|dsg|s ?tronic|steptronic|tiptronic)(?![a-z])", p)):
        return "manual"
    return None


def prevodovka_ze_slova(t):
    t = norm(t)
    if "manual" in t:
        return "manual"
    if "automat" in t or "dsg" in t:
        return "automat"
    return None


def inzerat(zdroj, nazev, url, cena, mena, rok=None, km=None, kw=None, lokalita="", popis="",
            prevodovka=None, karoserie=None, detail=None, text_lze_doplnit=False):
    cena_czk = int(cena * KURZY.get(mena, 1.0)) if cena else None
    return {"zdroj": zdroj, "nazev": (nazev or "").strip(), "url": url, "cena": cena, "mena": mena,
            "cena_czk": cena_czk, "rok": rok, "km": km, "kw": kw, "lokalita": (lokalita or "").strip(),
            "popis": popis or "", "prevodovka": prevodovka, "karoserie": karoserie,
            "detail": detail,                      # (typ, adresa) pro stažení detailu, nebo None
            "text_lze_doplnit": text_lze_doplnit,  # popis ve výpisu je zkrácený, detail ho doplní
            "odkazy": [(zdroj, url)]}


# ---------- zdroje: výpisy ----------
def bazos(auto):
    if not auto.get("hledat"):
        return []
    kategorie = (auto.get("bazos_kategorie") or "").strip("/")
    vysledky, videne = [], set()
    for crz in range(0, 400, 40):
        url = "https://auto.bazos.cz/" + (kategorie + "/" if kategorie else "") + (f"{crz}/" if crz else "")
        params = {"hledat": auto["hledat"], "hlokalita": "", "humkreis": "25", "cenaod": "",
                  "cenado": auto.get("cena_do") or ""}
        if crz:
            params["order"] = ""            # s Submit/kitx by Bazoš vrátil zase první stránku
        else:
            params.update({"rubriky": "auto", "Submit": "Hledat", "kitx": "ano"})
        bloky = BeautifulSoup(get(url, params=params).text, "html.parser").select("div.inzeraty")
        log(f"  Bazoš od {crz}: {len(bloky)} inzerátů")
        nove = 0
        for b in bloky:
            a = b.select_one("h2.nadpis a")
            if not a or not a.get("href"):
                continue
            url_i = urljoin("https://auto.bazos.cz/", a["href"])
            if url_i in videne:
                continue
            videne.add(url_i)
            nove += 1
            nazev = a.get_text(" ", strip=True)
            popis = text_z(b.select_one("div.popis"))
            cena, mena = cena_z_textu(text_z(b.select_one("div.inzeratycena")))
            lokalita = re.sub(r"\d{3}\s?\d{2}", "", text_z(b.select_one("div.inzeratylok"))).strip()
            vysledky.append(inzerat("Bazoš.cz", nazev, url_i, cena, mena, najdi_rok(nazev, popis),
                                    najdi_km(popis), najdi_kw(nazev, popis), lokalita, popis,
                                    najdi_prevodovku(nazev, popis), detail=("bazos", url_i),
                                    text_lze_doplnit=True))
        if len(bloky) < 40 or not nove:
            break
        time.sleep(PAUZA)
    return vysledky


def sauto(auto):
    if not auto.get("sauto_model"):
        return []
    extra = dict(auto.get("sauto_extra") or {})
    if auto.get("jen_manual"):
        extra.setdefault("gearbox_seo", "manualni")
    if auto.get("rok_od"):
        extra.setdefault("vehicle_age_from", auto["rok_od"])
    extra.setdefault("operating_lease", "false")
    karoserie = extra.get("vehicle_body_seo")
    kw_pevny = None
    if auto.get("kw"):                    # Sauto umí filtrovat výkon přímo, detail pak není potřeba
        extra.setdefault("engine_power_from", min(auto["kw"]))
        extra.setdefault("engine_power_to", max(auto["kw"]))
        if len(auto["kw"]) == 1:
            kw_pevny = auto["kw"][0]
    vysledky, videne = [], set()
    for offset in range(0, 2000, 100):
        params = {"limit": 100, "offset": offset, "category_id": 838,
                  "condition_seo": "nove,ojete,predvadeci",
                  "manufacturer_model_seo": auto["sauto_model"], **extra}
        if auto.get("cena_do"):
            params["price_to"] = auto["cena_do"]
        polozky = get("https://www.sauto.cz/api/v1/items/search", params=params).json().get("results") or []
        log(f"  Sauto od {offset}: {len(polozky)} inzerátů")
        if offset == 0 and polozky:
            log("  Sauto, ukázka první položky:\n" + json.dumps(polozky[0], ensure_ascii=False, indent=1)[:1500])
        for p in polozky:
            if p.get("id") in videne:
                continue
            videne.add(p.get("id"))
            nazev = p.get("name") or ""          # už obsahuje i additional_model_name
            vyrobce = (p.get("manufacturer_cb") or {}).get("seo_name", "")
            model = (p.get("model_cb") or {}).get("seo_name", "")
            lok = p.get("locality") or {}
            m = re.match(r"(\d{4})", str(p.get("manufacturing_date") or p.get("in_operation_date") or ""))
            vysledky.append(inzerat(
                "Sauto.cz", nazev, f"https://www.sauto.cz/osobni/detail/{vyrobce}/{model}/{p.get('id')}",
                None if p.get("price_by_agreement") else p.get("price"), "CZK",
                int(m.group(1)) if m else najdi_rok(nazev),
                p.get("tachometer"), p.get("engine_power") or kw_pevny or najdi_kw(nazev),
                lok.get("municipality") or lok.get("district") or lok.get("region") or "",
                prevodovka=prevodovka_ze_slova((p.get("gearbox_cb") or {}).get("seo_name", "")),
                karoserie=karoserie,
                detail=("sauto", f"https://www.sauto.cz/api/v1/items/{p.get('id')}")))
        if len(polozky) < 100:
            break
        time.sleep(PAUZA)
    return vysledky


def sbazar(auto):
    if not auto.get("hledat") or not auto.get("sbazar_kategorie"):
        return []
    vysledky, videne = [], set()
    for offset in range(0, 1000, 100):
        params = {"phrase": auto["hledat"], "limit": 100, "offset": offset,
                  "category_id": auto["sbazar_kategorie"]}
        polozky = get("https://www.sbazar.cz/api/v1/items/search", params=params).json().get("results") or []
        log(f"  Sbazar od {offset}: {len(polozky)} inzerátů")
        for p in polozky:
            if p.get("id") in videne:
                continue
            videne.add(p.get("id"))
            if p.get("is_advert_mirroring"):
                continue                         # kopie inzerátu ze Sauta, ten už máme
            shop = ((p.get("user") or {}).get("user_service") or {}).get("shop_url")
            if not shop:
                continue
            nazev = p.get("name") or ""
            lok = p.get("locality") or {}
            vysledky.append(inzerat(
                "Sbazar.cz", nazev, f"https://www.sbazar.cz/{shop}/detail/{p.get('seo_name')}",
                None if p.get("price_by_agreement") else p.get("price"), "CZK",
                najdi_rok(nazev), None, najdi_kw(nazev),
                lok.get("municipality") or lok.get("district") or "",
                prevodovka=najdi_prevodovku(nazev),
                detail=("sbazar", f"https://www.sbazar.cz/api/v1/items/{p.get('id')}"),
                text_lze_doplnit=True))
        if len(polozky) < 100:
            break
        time.sleep(PAUZA)
    return vysledky


def autobazar(auto):
    if not auto.get("autobazar_model"):
        return []
    vysledky, videne = [], set()
    for strana in range(1, 21):
        params = {"page": strana} if strana > 1 else {}    # page=1 vrací 404
        if auto.get("cena_do"):
            params["priceTo"] = int(auto["cena_do"] / KURZY["EUR"]) + 1
        if auto.get("rok_od"):
            params["yearFrom"] = auto["rok_od"]
        r = get(f"https://www.autobazar.eu/vysledky/osobne-vozidla/{auto['autobazar_model'].strip('/')}/",
                params=params)
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
        if not m:
            raise RuntimeError("stránka nemá __NEXT_DATA__ (změna webu nebo blokace)")
        sr = (json.loads(m.group(1)).get("props", {}).get("pageProps", {}).get("searchRecords") or {})
        zaznamy = sr.get("data") or []
        log(f"  Autobazar.eu strana {strana}: {len(zaznamy)} inzerátů, celkem {sr.get('total')}")
        nove = 0
        for p in zaznamy:
            if p.get("id") in videne:
                continue
            videne.add(p.get("id"))
            nove += 1
            cena = p.get("finalPrice") or p.get("priceCurrent") or p.get("price")
            rok = int(p["yearValue"]) if str(p.get("yearValue") or "").isdigit() else najdi_rok(p.get("title"))
            vysledky.append(inzerat(
                "Autobazar.eu", p.get("title"),
                f"https://www.autobazar.eu/detail/{p.get('sefName')}/{p.get('id')}/",
                cena, "EUR", rok, p.get("mileage"), p.get("enginePower") or najdi_kw(p.get("title")),
                (p.get("location") or {}).get("name", ""),
                popis=p.get("carEquipmentValue") or "",
                prevodovka=prevodovka_ze_slova(p.get("gearboxValue") or ""),
                karoserie=p.get("bodyworkValue")))
        if not zaznamy or not nove or len(videne) >= (sr.get("total") or 0):
            break
        time.sleep(PAUZA)
    return vysledky


def ps_na_kw(ps):
    return round(ps * 0.7355) if ps else None


def mobile_de(auto):
    zaklad = auto.get("mobile_url")
    if not zaklad:
        return []
    zaklad = re.sub(r"&pageNumber=\d+", "", zaklad)
    karoserie = "coupe" if "c=SportsCar" in zaklad else None
    prevodovka = "manual" if "tr=MANUAL_GEAR" in zaklad else ("automat" if "tr=AUTOMATIC_GEAR" in zaklad else None)
    vysledky, videne = [], set()
    for strana in range(1, 11):
        soup = BeautifulSoup(get(f"{zaklad}&pageNumber={strana}").text, "html.parser")
        nadpis = text_z(soup.select_one("h1"))
        if strana == 1 and re.match(r"0\s", nadpis):
            break                                 # nic nenalezeno, web pak ukazuje jen "podobné" inzeráty
        karty = soup.select('a[data-testid$="-link"][href*="details.html"]')
        log(f"  mobile.de strana {strana}: {len(karty)} inzerátů ({nadpis})")
        nove = 0
        for a in karty:
            m = re.search(r"[?&]id=(\d+)", a.get("href", ""))
            if not m or m.group(1) in videne:
                continue
            videne.add(m.group(1))
            vse = a.get_text(" ", strip=True)
            if "Anderes Modell" in vse or "Preis erweitert" in vse:
                continue                          # doporučení mimo zadané hledání
            nove += 1
            titulek = a.select_one('[data-testid$="-title"]')
            nazev = titulek.get_text(" ", strip=True) if titulek else vse[:80]
            cena, _ = cena_z_textu(text_z(a.select_one('[data-testid="price-label"]')))
            atributy = text_z(a.select_one('[data-testid="listing-details-attributes"]'))
            r = re.search(r"EZ\s*(?:\d{1,2}/)?(\d{4})", atributy)
            ps = re.search(r"(\d{2,3})\s*PS", atributy)
            lok = re.search(r"\d{5}\s+(.+)$", text_z(a.select_one('[data-testid="seller-info"]')))
            vysledky.append(inzerat(
                "mobile.de", nazev, f"https://suchen.mobile.de/fahrzeuge/details.html?id={m.group(1)}",
                cena, "EUR", int(r.group(1)) if r else None, najdi_km(atributy),
                najdi_kw(atributy) or ps_na_kw(int(ps.group(1)) if ps else None),
                ("DE, " + lok.group(1)) if lok else "DE", popis=atributy,
                prevodovka=prevodovka or najdi_prevodovku(nazev), karoserie=karoserie))
        if not karty or not nove:
            break
        time.sleep(PAUZA)
    return vysledky


def otomoto(auto):
    zaklad = auto.get("otomoto_url")
    if not zaklad:
        return []
    zaklad = re.sub(r"[?&]page=\d+", "", zaklad)
    karoserie = "coupe" if "coupe" in zaklad else None
    vysledky, videne = [], set()
    for strana in range(1, 11):
        url = zaklad + ("&" if "?" in zaklad else "?") + f"page={strana}"
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', get(url).text, re.S)
        if not m:
            raise RuntimeError("stránka nemá __NEXT_DATA__ (změna webu nebo blokace)")
        hledani = None
        for v in (json.loads(m.group(1)).get("props", {}).get("pageProps", {}).get("urqlState") or {}).values():
            d = json.loads(v.get("data") or "{}")
            if d.get("advertSearch"):
                hledani = d["advertSearch"]
                break
        if not hledani:
            break
        uzly = [e.get("node") or {} for e in hledani.get("edges") or []]
        log(f"  Otomoto strana {strana}: {len(uzly)} inzerátů, celkem {hledani.get('totalCount')}")
        nove = 0
        for n in uzly:
            if n.get("id") in videne:
                continue
            videne.add(n.get("id"))
            nove += 1
            par = {p.get("key"): p.get("value") for p in n.get("parameters") or []}
            castka = ((n.get("price") or {}).get("amount") or {})
            ps = int(par["engine_power"]) if str(par.get("engine_power") or "").isdigit() else None
            vysledky.append(inzerat(
                "Otomoto.pl", n.get("title"), n.get("url"), castka.get("units"), castka.get("currencyCode") or "PLN",
                int(par["year"]) if str(par.get("year") or "").isdigit() else None,
                int(par["mileage"]) if str(par.get("mileage") or "").isdigit() else None,
                ps_na_kw(ps), "PL, " + (((n.get("location") or {}).get("city") or {}).get("name") or ""),
                popis=n.get("shortDescription") or "",
                prevodovka=prevodovka_ze_slova(par.get("gearbox") or ""), karoserie=karoserie))
        if not uzly or not nove or len(videne) >= (hledani.get("totalCount") or 0):
            break
        time.sleep(PAUZA)
    return vysledky


ZDROJE = {"Bazoš.cz": bazos, "Sauto.cz": sauto, "Sbazar.cz": sbazar, "Autobazar.eu": autobazar,
          "mobile.de": mobile_de, "Otomoto.pl": otomoto}


# ---------- zdroje: detail inzerátu ----------
def detail_bazos(url):
    soup = BeautifulSoup(get(url).text, "html.parser")
    popis = text_z(soup.select_one("div.popisdetail"))
    nadpis = text_z(soup.select_one("h1.nadpisdetail"))
    return {"popis": popis, "rok": najdi_rok(nadpis, popis), "km": najdi_km(popis),
            "kw": najdi_kw(nadpis, popis), "prevodovka": najdi_prevodovku(nadpis, popis)}


def detail_sauto(url):
    j = get(url).json()
    r = j.get("result") or j
    popis = r.get("description") or ""
    m = re.match(r"(\d{4})", str(r.get("manufacturing_date") or ""))
    return {"popis": popis, "rok": int(m.group(1)) if m else None, "km": r.get("tachometer"),
            "kw": r.get("engine_power") or najdi_kw(r.get("name"), popis),
            "prevodovka": prevodovka_ze_slova((r.get("gearbox_cb") or {}).get("seo_name", "")),
            "karoserie": (r.get("vehicle_body_cb") or {}).get("name")}


def detail_sbazar(url):
    j = get(url).json()
    r = j.get("result") or j
    popis = r.get("description") or ""
    nazev = r.get("name") or ""
    return {"popis": popis, "rok": najdi_rok(nazev, popis), "km": najdi_km(popis),
            "kw": najdi_kw(nazev, popis), "prevodovka": najdi_prevodovku(nazev, popis)}


DETAILY = {"bazos": detail_bazos, "sauto": detail_sauto, "sbazar": detail_sbazar}


# ---------- filtr ----------
def sedi_karoserie(auto, i):
    if not auto.get("karoserie") or not i.get("karoserie"):
        return None
    k = norm(i["karoserie"])
    return any(norm(x) in k for x in auto["karoserie"])


def ma_klicove_slovo(auto, i):
    if not auto.get("musi_obsahovat"):
        return True
    vse = norm(i["nazev"] + " " + i["popis"])
    return any(obsahuje(vse, s) for s in auto["musi_obsahovat"]) or sedi_karoserie(auto, i) is True


def duvod_vyrazeni(auto, i, konecne=True):
    """konecne=False: předběžný filtr před stažením detailu, neznámé údaje nevadí."""
    nazev = norm(i["nazev"])
    if any(obsahuje(nazev, s) for s in auto.get("nesmi_obsahovat", [])):
        return "zakázané slovo v názvu"
    if auto.get("musi_v_nazvu") and not any(obsahuje(nazev, s) for s in auto["musi_v_nazvu"]):
        return "jiný model"
    if auto.get("cena_do") and i["cena_czk"] and i["cena_czk"] > auto["cena_do"]:
        return "vyšší cena"
    if i["cena_czk"] and i["cena_czk"] < CENA_MIN:
        return "nízká cena (díl?)"
    if auto.get("rok_od") and i["rok"] and i["rok"] < auto["rok_od"]:
        return "starší rok"
    if auto.get("kw") and i["kw"] and i["kw"] not in auto["kw"]:
        return "jiný výkon"
    if auto.get("jen_manual") and i.get("prevodovka") == "automat":
        return "automat"
    if sedi_karoserie(auto, i) is False:
        return "jiná karoserie"
    if (konecne or not i["text_lze_doplnit"]) and not ma_klicove_slovo(auto, i):
        return "chybí klíčové slovo"
    if konecne and not i["rok"] and not i["km"] and not i["text_lze_doplnit"]:
        return "bez roku i nájezdu (díl?)"
    return None


def potrebuje_detail(auto, i):
    if not i.get("detail"):
        return False
    return ((auto.get("kw") and not i["kw"]) or not i["km"] or not i["rok"]
            or (auto.get("jen_manual") and not i.get("prevodovka"))
            or (i["text_lze_doplnit"] and not ma_klicove_slovo(auto, i)))


def doplnit(i, d):
    for k in ("rok", "km", "kw", "prevodovka", "karoserie"):
        i[k] = i.get(k) or d.get(k)
    if len(d.get("popis") or "") > len(i["popis"]):
        i["popis"] = d["popis"]
    i["text_lze_doplnit"] = False


def sloucit(seznam):
    """Stejné auto na více webech: stejná cena v Kč, nekonfliktní rok, výkon a nájezd."""
    vysledek = []
    for i in seznam:
        for j in vysledek:
            if (i["cena_czk"] and i["cena_czk"] == j["cena_czk"]
                    and i["zdroj"] not in [z for z, _ in j["odkazy"]]
                    and (not i["rok"] or not j["rok"] or i["rok"] == j["rok"])
                    and (not i["kw"] or not j["kw"] or i["kw"] == j["kw"])
                    and (not i["km"] or not j["km"] or abs(i["km"] - j["km"]) < 5000)):
                j["odkazy"] += i["odkazy"]
                for k in ("rok", "km", "kw"):
                    j[k] = j[k] or i[k]
                break
        else:
            vysledek.append(i)
    return vysledek


# ---------- stav ----------
def nacti_stav(auto):
    soubor = SLOZKA_STAV / f"{auto['id']}.json"
    if not soubor.exists():
        return None
    try:
        return json.loads(soubor.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"   VAROVÁNÍ: {soubor.name} nejde přečíst ({e}), beru jako první běh")
        return None


def uloz_stav(auto, stav, inzeraty, detaily, souhrn):
    dnes = date.today().isoformat()
    hranice = (date.today() - timedelta(days=DNY_PAMATOVAT)).isoformat()
    stav = dict(stav or {})
    videne = dict(stav.get("inzeraty") or {})
    for i in inzeraty:
        for _, url in i["odkazy"]:
            videne[url] = {"cena": i["cena_czk"], "videno": dnes}
    stav["inzeraty"] = {u: v for u, v in videne.items() if v.get("videno", "") >= hranice}
    stav["detaily"] = {u: v for u, v in detaily.items() if v.get("videno", "") >= hranice}
    stav["souhrn"] = souhrn
    SLOZKA_STAV.mkdir(exist_ok=True)
    (SLOZKA_STAV / f"{auto['id']}.json").write_text(json.dumps(stav, ensure_ascii=False, indent=1),
                                                    encoding="utf-8")


# ---------- HTML ----------
TD = "border-bottom:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top"
TH = TD + ";background:#f4f4f4"
TABULKA = "border-collapse:collapse;width:100%;margin-bottom:24px;font-family:Segoe UI,Arial,sans-serif;font-size:14px"


def bunka_cena(i):
    if not i["cena"]:
        return "dohodou"
    if i["mena"] != "CZK":
        t = f"{cislo(i['cena'])} {MENY.get(i['mena'], i['mena'])}<br><small>≈ {cislo(i['cena_czk'])} Kč</small>"
    else:
        t = f"{cislo(i['cena'])} Kč"
    if i.get("puvodni"):
        t = f"<s>{cislo(i['puvodni'])} Kč</s><br>{t}"
    return t


def bunka_kw(auto, i):
    if not i["kw"]:
        return "neuvedeno ?"
    if not auto.get("kw"):
        return f"{i['kw']} kW"
    return f"{i['kw']} kW " + ("✔" if i["kw"] in auto["kw"] else "✘")


def tabulka(auto, seznam, s_duvodem=False):
    hlavicka = ["Zdroj", "Inzerát", "Cena", "Rok", "Nájezd", "Výkon", "Převodovka", "Lokalita"]
    if s_duvodem:
        hlavicka.insert(0, "Důvod")
    radky = ["<tr>" + "".join(f"<th style='{TH}'>{h}</th>" for h in hlavicka) + "</tr>"]
    if not seznam:
        radky.append(f"<tr><td style='{TD}' colspan={len(hlavicka)}><i>nic</i></td></tr>")
    for i in seznam:
        zdroje = "<br>".join(f"<a href='{html.escape(u)}'>{html.escape(z)}</a>" for z, u in i["odkazy"])
        bunky = [zdroje,
                 f"<a href='{html.escape(i['url'])}'>{html.escape(i['nazev'])}</a>",
                 bunka_cena(i),
                 str(i["rok"] or "?"),
                 f"{cislo(i['km'])} km" if i["km"] else "?",
                 bunka_kw(auto, i),
                 {"manual": "manuál", "automat": "automat"}.get(i.get("prevodovka"), "?"),
                 html.escape(i["lokalita"])]
        if s_duvodem:
            bunky.insert(0, html.escape(i.get("duvod", "")))
        radky.append("<tr>" + "".join(f"<td style='{TD}'>{b}</td>" for b in bunky) + "</tr>")
    return f"<table style='{TABULKA}'>" + "\n".join(radky) + "</table>"


def report(auto, inzeraty, nove, zlevnene, vyrazene, chyby, pocty, prvni_beh):
    nazev = html.escape(auto["nazev"])
    stazeno = ", ".join(f"{z} {n}" for z, n in pocty.items()) or "nic"
    out = ["<!doctype html><html lang=cs><meta charset=utf-8>",
           "<meta name=viewport content='width=device-width,initial-scale=1'>",
           f"<title>{nazev} | hlídač</title>",
           "<body style='font-family:Segoe UI,sans-serif;margin:24px;color:#222'>",
           "<p><a href='index.html'>← všechna auta</a></p>",
           f"<h1>{nazev}</h1>",
           f"<p>Kontrola: {ted():%d.%m.%Y %H:%M} &nbsp;|&nbsp; staženo: {stazeno}</p>"]
    for ch in chyby:
        out.append(f"<p style='color:#b00'>Chyba zdroje {html.escape(ch)}</p>")
    if prvni_beh:
        out.append("<p style='color:#888'>První běh pro toto auto. Nové a zlevněné inzeráty "
                   "se začnou hlásit od příští kontroly.</p>")
    out += [f"<h2>🆕 Nové inzeráty ({len(nove)})</h2>", tabulka(auto, nove),
            f"<h2>📉 Zlevněné ({len(zlevnene)})</h2>", tabulka(auto, zlevnene),
            f"<h2>Všechny vyhovující inzeráty ({len(inzeraty)})</h2>", tabulka(auto, inzeraty),
            f"<details><summary>Vyřazené inzeráty ({len(vyrazene)}), pro kontrolu filtru</summary>",
            tabulka(auto, vyrazene, s_duvodem=True), "</details>",
            "<p style='color:#888;font-size:13px'>„?“ = inzerát údaj neuvádí, stojí za ověření. "
            f"Cizí měny přepočtené kurzem ČNB (EUR {KURZY['EUR']:.2f}, PLN {KURZY['PLN']:.2f} Kč). "
            "Stejné auto z více webů je v jednom řádku.</p>"
            "</body></html>"]
    return "\n".join(out)


def index():
    radky = []
    for auto in AUTA:
        stav = nacti_stav(auto) or {}
        s = stav.get("souhrn") or {}
        nejlevnejsi = f"{cislo(s['nejlevnejsi'])} Kč" if s.get("nejlevnejsi") else ""
        chyby = f"<span style='color:#b00'>{len(s.get('chyby', []))} chyb</span>" if s.get("chyby") else ""
        radky.append(
            "<tr>" + "".join(f"<td style='{TD}'>{b}</td>" for b in [
                f"<a href='{html.escape(auto['id'])}.html'>{html.escape(auto['nazev'])}</a>",
                s.get("kontrola", "zatím neběželo"), s.get("vyhovuje", ""),
                f"<b>{s['nove']}</b>" if s.get("nove") else s.get("nove", ""),
                f"<b>{s['zlevnene']}</b>" if s.get("zlevnene") else s.get("zlevnene", ""),
                nejlevnejsi, chyby]) + "</tr>")
    hlavicka = ["Auto", "Kontrola", "Vyhovuje", "Nové", "Zlevněné", "Nejlevnější", ""]
    SLOZKA_REPORTY.mkdir(exist_ok=True)
    (SLOZKA_REPORTY / ".nojekyll").touch()
    (SLOZKA_REPORTY / "index.html").write_text(
        "<!doctype html><html lang=cs><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'><title>Hlídač aut</title>"
        "<body style='font-family:Segoe UI,sans-serif;margin:24px;color:#222'><h1>Hlídač aut</h1>"
        f"<table style='{TABULKA}'><tr>" + "".join(f"<th style='{TH}'>{h}</th>" for h in hlavicka) + "</tr>"
        + "\n".join(radky) + "</table></body></html>", encoding="utf-8")


# ---------- hlavní běh ----------
def zpracuj(auto):
    print(f"{auto['id']}:")
    stav = nacti_stav(auto)
    dnes = date.today().isoformat()
    cache = dict((stav or {}).get("detaily") or {})

    surove, chyby, pocty = [], [], {}
    for jmeno, funkce in ZDROJE.items():
        try:
            nalezeno = funkce(auto)
        except Exception as e:
            chyby.append(f"{jmeno}: {e}")
            continue
        pocty[jmeno] = len(nalezeno)
        surove += nalezeno

    # 1) předběžný filtr, 2) detail pro kandidáty, kterým něco chybí, 3) konečný filtr
    vyrazene, kandidati = [], []
    for i in surove:
        d = cache.get(i["url"])
        if d:
            doplnit(i, d)
            d["videno"] = dnes
        duvod = duvod_vyrazeni(auto, i, konecne=False)
        if duvod:
            i["duvod"] = duvod
            vyrazene.append(i)
        else:
            kandidati.append(i)

    stazeno_detailu, chyby_detailu = 0, 0
    for i in kandidati:
        if not potrebuje_detail(auto, i) or i["url"] in cache:
            continue
        if stazeno_detailu >= MAX_DETAILU:
            log(f"  limit {MAX_DETAILU} detailů, zbytek příště")
            break
        typ, adresa = i["detail"]
        try:
            d = DETAILY[typ](adresa)
            stazeno_detailu += 1
        except Exception as e:
            chyby_detailu += 1
            log(f"  detail selhal {adresa}: {e}")
            continue
        d["popis"] = (d.get("popis") or "")[:4000]
        d["videno"] = dnes
        cache[i["url"]] = d
        doplnit(i, d)
        log(f"  detail {i['zdroj']}: {i['nazev'][:60]} -> rok {i['rok']}, {i['km']} km, {i['kw']} kW, {i['prevodovka']}")
        time.sleep(PAUZA)

    prosle = []
    for i in kandidati:
        duvod = duvod_vyrazeni(auto, i, konecne=True)
        if duvod:
            i["duvod"] = duvod
            vyrazene.append(i)
        else:
            prosle.append(i)
    for i in vyrazene:
        log(f"  vyřazeno ({i['duvod']}): {i['zdroj']} | {i['nazev']}")

    prosle.sort(key=lambda i: (i["cena_czk"] is None, i["cena_czk"] or 0))
    inzeraty = sloucit(prosle)
    vyrazene.sort(key=lambda i: (i["duvod"], i["cena_czk"] or 0))

    videne = (stav or {}).get("inzeraty") or {}
    nove, zlevnene = [], []
    if stav is not None:
        for i in inzeraty:
            urls = [u for _, u in i["odkazy"]]
            if not any(u in videne for u in urls):
                nove.append(i)
                continue
            stare = [videne[u]["cena"] for u in urls if u in videne and videne[u].get("cena")]
            if stare and i["cena_czk"] and i["cena_czk"] < min(stare):
                i["puvodni"] = min(stare)
                zlevnene.append(i)

    SLOZKA_REPORTY.mkdir(exist_ok=True)
    (SLOZKA_REPORTY / f"{auto['id']}.html").write_text(
        report(auto, inzeraty, nove, zlevnene, vyrazene, chyby, pocty, stav is None), encoding="utf-8")
    ceny = [i["cena_czk"] for i in inzeraty if i["cena_czk"]]
    souhrn = {"kontrola": f"{ted():%d.%m.%Y %H:%M}", "vyhovuje": len(inzeraty),
              "nove": len(nove), "zlevnene": len(zlevnene), "nejlevnejsi": min(ceny) if ceny else None,
              "chyby": chyby}
    if pocty:
        uloz_stav(auto, stav, inzeraty, cache, souhrn)

    print(f"   staženo {pocty}, detailů {stazeno_detailu}"
          + (f" (selhalo {chyby_detailu})" if chyby_detailu else "")
          + f", vyhovuje {len(inzeraty)}, nové {len(nove)}, zlevněné {len(zlevnene)}")
    souhrn_vyrazeni = {}
    for i in vyrazene:
        souhrn_vyrazeni[i["duvod"]] = souhrn_vyrazeni.get(i["duvod"], 0) + 1
    if souhrn_vyrazeni:
        print("   vyřazeno: " + ", ".join(f"{k} {v}x" for k, v in souhrn_vyrazeni.items()))
    for ch in chyby:
        print("   CHYBA " + ch)
    return {"auto": auto, "inzeraty": inzeraty, "nove": nove, "zlevnene": zlevnene, "chyby": chyby,
            "prvni_beh": stav is None}


# ---------- e-mail ----------
def email_html(vysledky, web):
    celkem_nove = sum(len(v["nove"]) for v in vysledky)
    celkem_zlev = sum(len(v["zlevnene"]) for v in vysledky)
    out = ["<div style='font-family:Segoe UI,Arial,sans-serif;color:#222;max-width:900px'>",
           f"<p>Kontrola {ted():%d.%m.%Y %H:%M}: nových {celkem_nove}, zlevněných {celkem_zlev}.</p>"]
    if web:
        out.append(f"<p><a href='{html.escape(web)}'>Všechna auta na webu</a></p>")
    for v in vysledky:
        auto = v["auto"]
        oznaceni = {id(i): "NOVÉ" for i in v["nove"]}
        oznaceni.update({id(i): "ZLEVNĚNO" for i in v["zlevnene"]})
        # nové a zlevněné vždy nahoru, zbytek doplnit nejlevnějšími
        vyber = (v["nove"] + v["zlevnene"] + [i for i in v["inzeraty"] if id(i) not in oznaceni])[:MAIL_TOP]
        odkaz = f" <small>(<a href='{html.escape(web + auto['id'])}.html'>celý report</a>)</small>" if web else ""
        out.append(f"<h2 style='font-size:18px;margin:24px 0 8px'>{html.escape(auto['nazev'])}{odkaz}</h2>")
        out.append(f"<p style='margin:0 0 8px;color:#666'>Vyhovuje {len(v['inzeraty'])}, "
                   f"nové {len(v['nove'])}, zlevněné {len(v['zlevnene'])}</p>")
        for ch in v["chyby"]:
            out.append(f"<p style='color:#b00;margin:0'>Nefunguje {html.escape(ch.split(':')[0])}</p>")
        if not vyber:
            out.append("<p><i>Nic nevyhovuje.</i></p>")
            continue
        radky = []
        for i in vyber:
            stitek = oznaceni.get(id(i), "")
            barva = "#0a7d32" if stitek == "NOVÉ" else "#b05a00"
            stitek = f"<b style='color:{barva}'>{stitek}</b><br>" if stitek else ""
            udaje = " · ".join(x for x in [str(i["rok"] or ""), f"{cislo(i['km'])} km" if i["km"] else "",
                                           f"{i['kw']} kW" if i["kw"] else "", i["lokalita"]] if x)
            zdroje = ", ".join(f"<a href='{html.escape(u)}'>{html.escape(z)}</a>" for z, u in i["odkazy"])
            radky.append(f"<tr><td style='{TD}'>{stitek}<a href='{html.escape(i['url'])}'>{html.escape(i['nazev'])}</a>"
                         f"<br><small style='color:#666'>{html.escape(udaje)} · {zdroje}</small></td>"
                         f"<td style='{TD};white-space:nowrap'>{bunka_cena(i)}</td></tr>")
        out.append(f"<table style='{TABULKA}'>" + "".join(radky) + "</table>")
    out.append("</div>")
    predmet = f"Hlídač aut {ted():%d.%m. %H:%M}"
    if celkem_nove or celkem_zlev:
        predmet += f": {celkem_nove} nových, {celkem_zlev} zlevněných"
    return predmet, "\n".join(out)


def posli_email(vysledky):
    uzivatel, heslo, komu = (os.environ.get(k, "").strip() for k in ("SMTP_USER", "SMTP_HESLO", "EMAIL_KOMU"))
    if BEZ_MAILU or not (uzivatel and heslo and komu):
        print("E-mail se neposílá (chybí SMTP_USER, SMTP_HESLO nebo EMAIL_KOMU).")
        return
    web = os.environ.get("WEB_URL", "").strip()
    if web and not web.endswith("/"):
        web += "/"
    predmet, telo = email_html(vysledky, web)
    msg = EmailMessage()
    msg["Subject"] = predmet
    msg["From"] = f"Hlídač aut <{uzivatel}>"
    msg["To"] = ", ".join(a.strip() for a in komu.replace(";", ",").split(",") if a.strip())
    msg.set_content("Report je v HTML, otevři ho v klientovi, který HTML zobrazuje.")
    msg.add_alternative(telo, subtype="html")
    server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    with smtplib.SMTP_SSL(server, int(os.environ.get("SMTP_PORT", "465")), timeout=60) as s:
        s.login(uzivatel, heslo)
        s.send_message(msg)
    print(f"E-mail odeslán: {msg['To']}")


if __name__ == "__main__":
    vybrane = [a for a in sys.argv[1:] if not a.startswith("--")]
    nezname = [v for v in vybrane if v not in [a["id"] for a in AUTA]]
    if nezname:
        print("Neznámé id: " + ", ".join(nezname) + ". Dostupná: " + ", ".join(a["id"] for a in AUTA))
        sys.exit(1)
    nacti_kurzy()
    vysledky = []
    for auto in AUTA:
        if not vybrane or auto["id"] in vybrane:
            try:
                vysledky.append(zpracuj(auto))
            except Exception as e:
                print(f"   CHYBA při zpracování {auto['id']}: {e}")
    index()
    print(f"Hotovo, přehled: {SLOZKA_REPORTY / 'index.html'}")
    posli_email(vysledky)
