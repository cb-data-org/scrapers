"""What each platform shows next to the files, written from the snapshot's manifest: the Hugging Face dataset
card (README.md: YAML metadata, then Portuguese and English), Kaggle's dataset-metadata.json and Zenodo's
metadata (.zenodo.json). The licence, sources and format texts are the /dados page's own (apps/site,
DataPage.astro). Two column texts are corrected for the shops, which hold most rows (copywriter, 15 Oct 2026):
a price is not always a menu price, and a shop's price leaves after 14 days; /dados should say the same.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .snapshot import Manifest

BAHIA = ZoneInfo("America/Bahia")
HOME = "https://cervejabarata.com.br"
DADOS = f"{HOME}/dados"
DATA_EN = f"{HOME}/en/data"
METHODOLOGY = f"{HOME}/metodologia"
METHODOLOGY_EN = f"{HOME}/en/methodology"
ODBL = "https://opendatacommons.org/licenses/odbl/1-0/"
CONTACT = "contato@cervejabarata.com.br"
#: The brand accounts' datasets (CB_HF_DATASET and CB_KAGGLE_DATASET override them).
HF_DATASET = "cervejabarata/precos-de-cerveja"
KAGGLE_DATASET = "cervejabarata/precos-de-cerveja"
#: The brand stays national and the titles name no place; the coverage is in the subtitle and the text.
TITLE = "Cerveja Barata: preços de cerveja (dados abertos)"
#: Kaggle: a title of 6 to 50 characters and a subtitle of 20 to 80.
TITLE_EN = "Cerveja Barata: beer prices (open data)"
SUBTITLE_FALLBACK = "Beer prices per litre in bars, supermarkets and drinks shops in Brazil"
ZENODO_TITLE = "Cerveja Barata: beer prices, open data snapshot"
KEYWORDS = ["beer", "prices", "price per litre", "price index", "Salvador", "Bahia", "Brazil", "open data"]
STATES = {"BA": "Bahia"}
MONTHS_PT = [
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
]

#: The house warning (apps/site/src/brand.ts, WARNINGS), at the end of each language.
WARNING = {
    "pt": "Beba com moderação · Se beber, não dirija · Proibido para menores de 18 anos",
    "en": "Drink responsibly · Don't drink and drive · Not for anyone under 18",
}

SOURCES = {
    "pt": [
        "Preços de notas fiscais: Preço da Hora Bahia, Secretaria da Fazenda do Estado da Bahia (SEFAZ-BA).",
        "Bares e localizações: derivados do OpenStreetMap, © colaboradores do OpenStreetMap (ODbL), "
        "e dos lugares do Overture Maps, © Overture Maps Foundation (CDLA Permissive 2.0).",
        "Limites de bairros: GeoSalvador, Prefeitura Municipal de Salvador (Salvador); IBGE, "
        "Censo 2022 (Lauro de Freitas).",
    ],
    "en": [
        "Receipt prices: Preço da Hora Bahia, Bahia State Treasury (SEFAZ-BA).",
        "Bars and their locations: derived from OpenStreetMap, © OpenStreetMap contributors (ODbL), "
        "and from Overture Maps' places, © Overture Maps Foundation (CDLA Permissive 2.0).",
        "Neighbourhood boundaries: GeoSalvador, Salvador City Council (Salvador); IBGE, 2022 Census "
        "(Lauro de Freitas).",
    ],
}

LICENCE = {
    "pt": (
        f"Os dados são abertos sob a [Open Database License (ODbL) 1.0]({ODBL}): você pode copiar, "
        "adaptar e publicar, inclusive com fins comerciais, desde que cite a fonte e mantenha abertas, "
        "sob a mesma licença, as bases feitas a partir deles."
    ),
    "en": (
        f"The data is open under the [Open Database License (ODbL) 1.0]({ODBL}): copy, adapt and "
        "publish it, commercially too, as long as you credit the source and keep databases made from it "
        "open under the same licence."
    ),
}

PERSONAL = {
    "pt": "Nenhum dado pessoal é publicado: nada de e-mails, IPs ou nomes de quem mandou um preço.",
    "en": "No personal data is published: no emails, no IP addresses, no names of whoever sent a price.",
}

FORMATS = {
    "pt": (
        "Os arquivos JSON trazem as mesmas linhas com os nomes de campo do nosso banco (dinheiro em "
        "centavos, datas em unix); os CSV usam vírgula, ponto decimal e UTF-8, com dinheiro em reais e "
        "datas locais."
    ),
    "en": (
        "The JSON files carry the same rows with our database's field names (money in cents, unix "
        "times); the CSV files use commas, a decimal point and UTF-8, with money in reais and local dates."
    ),
}

#: Each dataset in Portuguese and English (most places with prices are shops).
DATASETS = {
    "deals": (
        "Preços: lugar, cerveja, formato, preço, preço por litro e a data da última verificação",
        "Prices: place, beer, format, price, price per litre and when each was last verified",
    ),
    "venues": ("Lugares com preço", "Places with prices"),
    "venues_all": ("Todos os lugares listados, com ou sem preço", "Every listed place, priced or not"),
    "price_index": (
        "Índice mensal dos bares: preço mediano por litro, para a cidade e por formato, bairro e marca",
        "Monthly bar index: median price per litre, for the city and by format, neighbourhood and brand",
    ),
    "neighbourhoods": ("Limites dos bairros, em GeoJSON", "Neighbourhood boundaries, as GeoJSON"),
    "catalog": ("Catálogo de marcas, cervejas e formatos", "Catalog of brands, beers and formats"),
    "cities": ("Cidades cobertas", "Covered cities"),
}

#: The columns of deals.csv the /dados page explains.
COLUMNS = {
    "pt": [
        ("price_brl", "Preço, em reais, do que a linha descreve (ex.: um balde com 5)"),
        ("price_per_litre_brl", "O preço por litro, que usamos no ranking"),
        (
            "format_code, units, packaging",
            "Como vem: formato (garrafa_600), quantidade e single, balde, torre, combo ou dobro",
        ),
        (
            "kind",
            "regular, happy_hour, promo, combo ou open_bar (open bar é comparado por preço e duração)",
        ),
        (
            "schedules",
            'Quando o preço vale, no horário da cidade: "Fri 17:00-20:00; Sat 22:00-02:00"; vazio quer '
            "dizer sempre que o bar está aberto (num happy_hour ou promo: dias e horários ainda a "
            "confirmar)",
        ),
        (
            "service_charge_pct, couvert_brl",
            "Taxa de serviço e couvert, cobrados à parte do preço de cardápio",
        ),
        (
            "last_verified_on",
            "Última vez que o preço foi conferido; nos bares, depois de 90 dias pode estar desatualizado e "
            "depois de 180 sai do ranking; nas lojas, sai depois de 14 dias",
        ),
    ],
    "en": [
        ("price_brl", "Price, in reais, for what the row describes (e.g. a bucket of 5)"),
        ("price_per_litre_brl", "The price per litre we rank by"),
        (
            "format_code, units, packaging",
            "Serving: format (garrafa_600), how many, and single, balde, torre, combo or dobro",
        ),
        ("kind", "regular, happy_hour, promo, combo or open_bar (open bars rank by price and length)"),
        (
            "schedules",
            'When the price applies, in the city\'s time: "Fri 17:00-20:00; Sat 22:00-02:00"; empty '
            "means whenever the bar is open (for a happy_hour or promo: days and hours not known yet)",
        ),
        ("service_charge_pct, couvert_brl", "Service charge and cover, on top of the menu price"),
        (
            "last_verified_on",
            "The last time the price was checked; at bars, past 90 days it may be out of date and past "
            "180 it leaves the ranking; at shops, it leaves after 14 days",
        ),
    ],
}


def data_date(manifest: Manifest) -> datetime:
    """When the site's build wrote the data, in Salvador's time."""
    return datetime.fromisoformat(manifest.data_updated_at.replace("Z", "+00:00")).astimezone(BAHIA)


def date_pt(moment: datetime) -> str:
    """ "1º de novembro de 2026", "15 de outubro de 2026"."""
    day = "1º" if moment.day == 1 else str(moment.day)
    return f"{day} de {MONTHS_PT[moment.month - 1]} de {moment.year}"


def date_en(moment: datetime) -> str:
    return f"{moment.day} {moment:%B %Y}"


def citation(manifest: Manifest, lang: str) -> str:
    """As /dados words it, with the data's date."""
    moment = data_date(manifest)
    if lang == "pt":
        return f"Cerveja Barata (cervejabarata.com.br), dados de {date_pt(moment)}, licença ODbL 1.0."
    return f"Cerveja Barata (cervejabarata.com.br), data from {date_en(moment)}, ODbL 1.0 licence."


def listing(names: list[str], word: str) -> str:
    """ "A", "A e B", "A, B e C" (or with "and")."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} {word} {names[-1]}"


def coverage(manifest: Manifest, lang: str) -> str:
    """ "Salvador e Lauro de Freitas (Bahia)": the covered cities and their states."""
    word = "e" if lang == "pt" else "and"
    names = [str(city["name"]) for city in manifest.cities]
    codes = list(dict.fromkeys(str(city.get("state") or "") for city in manifest.cities if city.get("state")))
    states = listing([STATES.get(code, code) for code in codes], word) if codes else ""
    return f"{listing(names, word)} ({states})" if states else listing(names, word)


def subtitle(manifest: Manifest) -> str:
    """Kaggle's subtitle (20 to 80 characters), with the coverage while it fits."""
    text = f"Beer prices per litre in bars and shops in {coverage(manifest, 'en')}"
    text = text.replace(" (", ", ").removesuffix(")")
    return text if len(text) <= 80 else SUBTITLE_FALLBACK


def has_index(manifest: Manifest) -> bool:
    return any(f.dataset == "price_index" and f.rows for f in manifest.files)


def _split(name: str) -> str:
    """A city's slug as a Hugging Face split name (letters, digits and underscores)."""
    return name.replace("-", "_")


def _city(manifest: Manifest, slug: str | None) -> str | None:
    return next((str(c.get("name")) for c in manifest.cities if c.get("slug") == slug), None)


def _describe(manifest: Manifest, dataset: str, city: str | None, lang: str) -> str:
    what = DATASETS.get(dataset, ("", ""))[0 if lang == "pt" else 1]
    name = _city(manifest, city)
    return f"{what} ({name})" if name else what


def size_category(rows: int) -> str:
    for limit, label in ((1_000, "n<1K"), (10_000, "1K<n<10K"), (100_000, "10K<n<100K")):
        if rows < limit:
            return label
    return "100K<n<1M"


def configs(manifest: Manifest) -> list[str]:
    """The viewer's tables: one per dataset, one split per city, CSV files with rows only."""
    lines = ["configs:"]
    for dataset in ("deals", "venues", "venues_all", "price_index"):
        files = [f for f in manifest.files if f.dataset == dataset and f.format == "csv" and f.rows]
        if not files:
            continue
        lines.append(f"  - config_name: {dataset}")
        if dataset == "deals":
            lines.append("    default: true")
        lines.append("    data_files:")
        for f in files:
            lines += [f"      - split: {_split(f.city or 'all')}", f"        path: {f.name}"]
    return lines


def _files_table(manifest: Manifest, lang: str) -> str:
    pt = lang == "pt"
    lines = ["| Arquivo | O que tem | Linhas |" if pt else "| File | What it holds | Rows |", "|---|---|---|"]
    for f in manifest.files:
        rows = "" if f.rows is None else f"{f.rows:,}".replace(",", "." if pt else ",")
        lines.append(f"| `{f.name}` | {_describe(manifest, f.dataset, f.city, lang)} | {rows} |")
    about = (
        "Data da cópia; origem, tamanho e SHA-256 de cada arquivo"
        if pt
        else "Date of the snapshot; source, size and SHA-256 of each file"
    )
    lines.append(f"| `manifest.json` | {about} | |")
    return "\n".join(lines) + "\n"


def _columns(lang: str) -> str:
    title = "Colunas de deals.csv" if lang == "pt" else "Columns of deals.csv"
    return f"### {title}\n\n" + "".join(f"- `{name}`: {meaning}\n" for name, meaning in COLUMNS[lang])


def _section(manifest: Manifest, lang: str, intro: str) -> str:
    pt = lang == "pt"
    sources = "".join(f"- {source}\n" for source in SOURCES[lang])
    return (
        f"{intro}\n\n{_files_table(manifest, lang)}\n{FORMATS[lang]}\n\n"
        f"### {'Como citar' if pt else 'How to cite'}\n\n`{citation(manifest, lang)}`\n\n"
        f"### {'Licença' if pt else 'Licence'}\n\n{LICENCE[lang]}\n\n{sources}\n{PERSONAL[lang]}\n\n"
        f"{_columns(lang)}\n{'Contato' if pt else 'Contact'}: {CONTACT}\n\n*{WARNING[lang]}*\n\n"
    )


def body(manifest: Manifest) -> str:
    """The Markdown Hugging Face shows after the YAML and Kaggle shows as the description."""
    moment = data_date(manifest)
    intro_pt = (
        "Preços de cerveja em bares, mercados e depósitos, com o preço por litro, publicados pelo "
        f"[Cerveja Barata]({HOME}). Esta é uma cópia congelada dos dados abertos do site, como "
        f"estavam em {date_pt(moment)}. Cobertura: {coverage(manifest, 'pt')}. Os arquivos ao vivo, "
        f"atualizados a cada publicação do site, estão em [cervejabarata.com.br/dados]({DADOS}); a "
        f"metodologia, em [cervejabarata.com.br/metodologia]({METHODOLOGY})."
    )
    intro_en = (
        "Beer prices in bars, supermarkets and depósitos (drinks shops), with the price per litre, "
        f"published by [Cerveja Barata]({HOME}). This is a frozen snapshot of the site's open data, as "
        f"it stood on {date_en(moment)}. Coverage: {coverage(manifest, 'en')}, Brazil. The live files, "
        f"updated whenever the site is published, are at [cervejabarata.com.br/en/data]({DATA_EN}); the "
        f"methodology is at [cervejabarata.com.br/en/methodology]({METHODOLOGY_EN})."
    )
    return (
        f"# {TITLE}\n\n**Português** · [English](#english)\n\n{_section(manifest, 'pt', intro_pt)}"
        f"## English\n\n{_section(manifest, 'en', intro_en)}"
    )


def dataset_card(manifest: Manifest) -> str:
    """Hugging Face's README.md."""
    deals = sum(f.rows or 0 for f in manifest.files if f.dataset == "deals" and f.format == "csv")
    tags = ("beer", "prices", "price-index", "brazil", "salvador", "bahia", "open-data")
    header = [
        "---",
        "license: odbl",
        "language:",
        "  - pt",
        "  - en",
        f"pretty_name: {json.dumps(TITLE, ensure_ascii=False)}",
        "tags:",
        *(f"  - {tag}" for tag in tags),
        "size_categories:",
        f"  - {size_category(deals)}",
        *configs(manifest),
        "---",
        "",
    ]
    return "\n".join(header) + body(manifest)


def kaggle_metadata(manifest: Manifest, dataset_id: str = KAGGLE_DATASET) -> dict[str, Any]:
    resources = [
        {"path": f.name, "description": _describe(manifest, f.dataset, f.city, "en")} for f in manifest.files
    ]
    return {
        "title": TITLE_EN,
        "subtitle": subtitle(manifest),
        "description": body(manifest),
        "id": dataset_id,
        "licenses": [{"name": "ODbL-1.0"}],
        "keywords": ["beer", "alcohol", "brazil", "economics", "retail and shopping", "food"],
        "resources": resources,
        "expectedUpdateFrequency": "monthly",
        "userSpecifiedSources": manifest.attribution,
    }


def _html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def zenodo_metadata(manifest: Manifest) -> dict[str, Any]:
    """The deposition's metadata (.zenodo.json's format): the organisation as creator, ODbL, links back.
    The index is mentioned only once it has a month."""
    moment = data_date(manifest)
    index = has_index(manifest)
    index_en = " and a monthly index of median prices per litre in bars" if index else ""
    index_pt = " e um índice mensal do preço mediano por litro nos bares" if index else ""
    sources = "".join(f"<li>{_html(source)}</li>" for source in SOURCES["en"])
    live_en = f'<a href="{DATA_EN}">cervejabarata.com.br/en/data</a>'
    live_pt = f'<a href="{DADOS}">cervejabarata.com.br/dados</a>'
    description = (
        "<p>Beer prices in bars, supermarkets and depósitos (drinks shops) of "
        f"{_html(coverage(manifest, 'en'))}, Brazil, with the price per litre{index_en}, published by "
        f'<a href="{HOME}">Cerveja Barata</a>. A frozen snapshot of the site\'s open data, as it stood on '
        f"{date_en(moment)}; the live files are at {live_en}.</p>"
        "<p>Preços de cerveja em bares, mercados e depósitos de "
        f"{_html(coverage(manifest, 'pt'))}, com o preço por litro{index_pt}. Cópia congelada dos dados "
        f"abertos do site, como estavam em {date_pt(moment)}; os arquivos ao vivo estão em {live_pt}.</p>"
        f"<p>How to cite: {_html(citation(manifest, 'en'))}</p><p>Sources:</p><ul>{sources}</ul>"
        f"<p>{_html(PERSONAL['en'])}</p><p>{_html(WARNING['en'])}. {_html(WARNING['pt'])}.</p>"
    )
    day = moment.date().isoformat()
    return {
        "upload_type": "dataset",
        "title": ZENODO_TITLE,
        "creators": [{"name": "Cerveja Barata"}],
        "description": description,
        "access_right": "open",
        "license": "odbl-1.0",
        "keywords": KEYWORDS,
        "language": "por",
        "version": day,
        "publication_date": day,
        "related_identifiers": [
            {"identifier": DADOS, "relation": "isDerivedFrom", "resource_type": "dataset"},
            {"identifier": METHODOLOGY, "relation": "isDocumentedBy", "resource_type": "publication-other"},
        ],
        "notes": f"Attribution: {_html(manifest.attribution)}. Contact: {CONTACT}.",
    }


def write(out: Path, manifest: Manifest, kaggle_id: str = KAGGLE_DATASET) -> None:
    """README.md, dataset-metadata.json and .zenodo.json next to the files folder."""
    (out / "README.md").write_text(dataset_card(manifest))
    kaggle = json.dumps(kaggle_metadata(manifest, kaggle_id), ensure_ascii=False, indent=2)
    (out / "dataset-metadata.json").write_text(kaggle + "\n")
    zenodo = json.dumps(zenodo_metadata(manifest), ensure_ascii=False, indent=2)
    (out / ".zenodo.json").write_text(zenodo + "\n")
