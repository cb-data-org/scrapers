"""CNAE 2.3 subclasses (IBGE) the venue classifier meets, with their official Portuguese names, so the LLM
reads what a company registered rather than a bare code. Codes not listed go to the model as the code
alone."""

_GENERAL = "Comércio varejista de mercadorias em geral, com predominância de produtos alimentícios - "

DESCRIPTIONS: dict[int, str] = {
    # Food and drink service
    5611201: "Restaurantes e similares",
    5611202: "Bares e outros estabelecimentos especializados em servir bebidas",
    5611203: "Lanchonetes, casas de chá, de sucos e similares",
    5611204: "Bares e outros estabelecimentos especializados em servir bebidas, sem entretenimento",
    5611205: "Bares e outros estabelecimentos especializados em servir bebidas, com entretenimento",
    5612100: "Serviços ambulantes de alimentação",
    5620101: "Fornecimento de alimentos preparados preponderantemente para empresas",
    5620102: "Serviços de alimentação para eventos e recepções - bufê",
    5620103: "Cantinas - serviços de alimentação privativos",
    5620104: "Fornecimento de alimentos preparados preponderantemente para consumo domiciliar",
    # Breweries
    1113502: "Fabricação de cervejas e chopes",
    # Retail: drinks, food, stores
    4723700: "Comércio varejista de bebidas",
    4711301: _GENERAL + "hipermercados",
    4711302: _GENERAL + "supermercados",
    4712100: _GENERAL + "minimercados, mercearias e armazéns",
    4713004: "Lojas de departamentos ou magazines, exceto lojas francas (duty free)",
    4721101: "Padaria e confeitaria com predominância de produção própria",
    4721102: "Padaria e confeitaria com predominância de revenda",
    4721103: "Comércio varejista de laticínios e frios",
    4721104: "Comércio varejista de doces, balas, bombons e semelhantes",
    4722901: "Comércio varejista de carnes - açougues",
    4722902: "Peixaria",
    4724500: "Comércio varejista de hortifrutigranjeiros",
    4729601: "Tabacaria",
    4729602: "Comércio varejista de mercadorias em lojas de conveniência",
    4729699: (
        "Comércio varejista de produtos alimentícios em geral ou especializado em produtos alimentícios não "
        "especificados anteriormente"
    ),
    4731800: "Comércio varejista de combustíveis para veículos automotores",
    4732600: "Comércio varejista de lubrificantes",
    4789099: "Comércio varejista de outros produtos não especificados anteriormente",
    1091101: "Fabricação de produtos de panificação industrial",
    1091102: "Fabricação de produtos de padaria e confeitaria com predominância de produção própria",
    # Wholesale
    4635401: "Comércio atacadista de água mineral",
    4635402: "Comércio atacadista de cerveja, chope e refrigerante",
    4635403: "Comércio atacadista de bebidas com atividade de fracionamento e acondicionamento associada",
    4635499: "Comércio atacadista de bebidas não especificadas anteriormente",
    4637199: (
        "Comércio atacadista especializado em outros produtos alimentícios não especificados anteriormente"
    ),
    4639701: "Comércio atacadista de produtos alimentícios em geral",
    4691500: "Comércio atacadista de mercadorias em geral, com predominância de produtos alimentícios",
    # Lodging, leisure, clubs, events
    5510801: "Hotéis",
    5510802: "Apart-hotéis",
    5590601: "Albergues, exceto assistenciais",
    5590699: "Outros alojamentos não especificados anteriormente",
    8230001: "Serviços de organização de feiras, congressos, exposições e festas",
    8230002: "Casas de festas e eventos",
    9001999: "Artes cênicas, espetáculos e atividades complementares não especificadas anteriormente",
    9311500: "Gestão de instalações de esportes",
    9312300: "Clubes sociais, esportivos e similares",
    9319101: "Produção e promoção de eventos esportivos",
    9329801: "Discotecas, danceterias, salões de dança e similares",
    9329899: "Outras atividades de recreação e lazer não especificadas anteriormente",
    9430800: "Atividades de associações de defesa de direitos sociais",
    9493600: "Atividades de organizações associativas ligadas à cultura e à arte",
    9499500: "Atividades associativas não especificadas anteriormente",
}


def code_text(code: int) -> str:
    """5611204 → "5611-2/04", the way the register writes it."""
    digits = f"{code:07d}"
    return f"{digits[:4]}-{digits[4]}/{digits[5:]}"


def describe(code: int) -> str:
    """ "5611-2/04 Bares e outros …" (or the code alone when unknown)."""
    name = DESCRIPTIONS.get(code)
    return f"{code_text(code)} {name}" if name else code_text(code)
