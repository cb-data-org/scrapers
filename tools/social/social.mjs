// Generated from tools/scripts/src/social.ts (cervejabarata.com.br): don't edit.

// tools/scripts/src/social.ts
import { mkdirSync, readFileSync as readFileSync2, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join as join2 } from "node:path";
import { parseArgs } from "node:util";

// apps/site/src/data/snapshot.ts
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
function readSnapshot(dir) {
  const read = (name) => {
    const path2 = join(dir, `${name}.json`);
    if (!existsSync(path2)) throw new Error(`${path2} is missing: run \`pnpm snapshot\` first`);
    return JSON.parse(readFileSync(path2, "utf8"));
  };
  const beers = read("v_public_beers");
  return {
    manifest: read("manifest"),
    cities: read("v_public_cities"),
    neighbourhoods: read("v_public_neighbourhoods"),
    venues: read("v_public_venues"),
    deals: withAlcoholFlag(read("v_public_deals"), beers),
    brands: read("v_public_brands"),
    beers,
    formats: read("v_public_formats"),
    priceIndex: read("v_public_price_index"),
    officialSeries: read("v_public_official_series"),
    sponsorships: read("v_public_sponsorships"),
    // Newer than some saved snapshots (the end-to-end fixture): none there.
    credits: existsSync(join(dir, "v_public_credits.json")) ? read("v_public_credits") : []
  };
}
function withAlcoholFlag(deals, beers) {
  const free = new Set(beers.filter((beer) => beer.non_alcoholic).map((beer) => beer.slug));
  return deals.map(
    (row) => row.non_alcoholic === void 0 || row.non_alcoholic === null ? { ...row, non_alcoholic: free.has(row.beer) ? 1 : 0 } : row
  );
}

// packages/domain/src/formats.ts
var format = (type, volumeMl, pt, en) => ({
  code: `${type}_${volumeMl}`,
  type,
  volumeMl,
  label: { "pt-BR": pt, en }
});
var FORMATS = [
  format("lata", 269, "Lata 269 ml", "Can 269 ml"),
  format("lata", 350, "Lata 350 ml", "Can 350 ml"),
  format("lata", 473, "Lat\xE3o 473 ml", "Tall can 473 ml"),
  format("long_neck", 275, "Long neck 275 ml", "Bottle 275 ml"),
  format("long_neck", 330, "Long neck 330 ml", "Bottle 330 ml"),
  format("long_neck", 350, "Long neck 350 ml", "Bottle 350 ml"),
  format("long_neck", 355, "Long neck 355 ml", "Bottle 355 ml"),
  format("garrafa", 300, "Garrafa 300 ml (litrinho)", "Bottle 300 ml"),
  format("garrafa", 600, "Garrafa 600 ml", "Bottle 600 ml"),
  format("litrao", 1e3, "Litr\xE3o 1 L", "Litre bottle 1 L"),
  format("chopp", 300, "Chopp 300 ml", "Draught 300 ml"),
  format("chopp", 330, "Chopp 330 ml", "Draught 330 ml"),
  format("chopp", 350, "Chopp 350 ml", "Draught 350 ml"),
  format("chopp", 400, "Chopp 400 ml", "Draught 400 ml"),
  format("chopp", 473, "Chopp 473 ml", "Draught 473 ml"),
  format("chopp", 500, "Chopp 500 ml", "Draught 500 ml"),
  format("torre", 2500, "Torre 2,5 L", "Beer tower 2.5 L"),
  format("torre", 3e3, "Torre 3 L", "Beer tower 3 L"),
  format("pint", 568, "Pint 568 ml", "Pint"),
  format("half_pint", 284, "Meio pint 284 ml", "Half pint")
];
var TYPE_BY_VOLUME_ML = {
  269: "lata",
  275: "long_neck",
  284: "half_pint",
  300: "garrafa",
  330: "long_neck",
  350: "lata",
  355: "long_neck",
  400: "chopp",
  473: "lata",
  500: "chopp",
  568: "pint",
  600: "garrafa",
  1e3: "litrao"
};
function formatByCode(code) {
  return FORMATS.find((candidate) => candidate.code === code);
}

// packages/domain/src/money.ts
var moneyFormats = /* @__PURE__ */ new Map();
function formatMoney(minor, market2) {
  const key = `${market2.locale} ${market2.currency}`;
  let format2 = moneyFormats.get(key);
  if (!format2) {
    format2 = new Intl.NumberFormat(market2.locale, { style: "currency", currency: market2.currency });
    moneyFormats.set(key, format2);
  }
  return format2.format(minor / 100);
}

// packages/domain/src/parse.ts
var BARE_VOLUME = new RegExp(`\\b(${Object.keys(TYPE_BY_VOLUME_ML).join("|")})\\b`);

// packages/domain/src/schedule.ts
var DAY = 1440;
var DAY_NAMES = {
  "pt-BR": ["dom", "seg", "ter", "qua", "qui", "sex", "s\xE1b"],
  en: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
};
var EVERY_DAY = { "pt-BR": "todos os dias", en: "every day" };
function clock(minute, locale) {
  const hours = String(Math.floor(minute % DAY / 60)).padStart(2, "0");
  const minutes = String(minute % 60).padStart(2, "0");
  if (locale === "en") return `${hours}:${minutes}`;
  return minutes === "00" ? `${hours}h` : `${hours}h${minutes}`;
}
var weekPosition = (dow) => dow === 0 ? 7 : dow;
function describeWindows(windows, locale = "pt-BR") {
  const names = DAY_NAMES[locale];
  const daysByHours = /* @__PURE__ */ new Map();
  for (const window of [...windows].sort((a, b) => weekPosition(a.dow) - weekPosition(b.dow))) {
    const hours = `${clock(window.startMin, locale)}\u2013${clock(window.endMin, locale)}`;
    const days = daysByHours.get(hours) ?? [];
    if (!days.includes(window.dow)) days.push(window.dow);
    daysByHours.set(hours, days);
  }
  const groups = [];
  for (const [hours, days] of daysByHours) {
    if (days.length === 7) {
      groups.push(`${EVERY_DAY[locale]} ${hours}`);
      continue;
    }
    const runs = [];
    for (const day of days) {
      const run = runs.at(-1);
      const previous = run?.at(-1);
      if (run && previous !== void 0 && weekPosition(day) === weekPosition(previous) + 1) run.push(day);
      else runs.push([day]);
    }
    const dayText = runs.map(
      (run) => run.length >= 3 ? `${names[run[0] ?? 0]}\u2013${names[run.at(-1) ?? 0]}` : run.map((day) => names[day]).join(", ")
    ).join(", ");
    groups.push(`${dayText} ${hours}`);
  }
  return groups.join(" \xB7 ");
}

// packages/domain/src/enums.ts
function venueCategory(kind, onSite) {
  if (kind === "deposito") return "deposito";
  return onSite === false || onSite === 0 ? "mercado" : "bar";
}
function dealCategory(kind, venueOnSite, dealOnSite) {
  if (dealOnSite === true || dealOnSite === 1) return "bar";
  if (dealOnSite === false || dealOnSite === 0) return kind === "deposito" ? "deposito" : "mercado";
  return venueCategory(kind, venueOnSite);
}

// packages/domain/src/freshness.ts
var FRESHNESS_DAYS = { verified: 30, dated: 90, stale: 180 };
var dayFormatters = /* @__PURE__ */ new Map();
function localDateParts(unixSeconds, timeZone) {
  let formatter = dayFormatters.get(timeZone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    });
    dayFormatters.set(timeZone, formatter);
  }
  const parts = formatter.formatToParts(new Date(unixSeconds * 1e3));
  const value = (type) => Number(parts.find((part) => part.type === type)?.value);
  return { year: value("year"), month: value("month"), day: value("day") };
}
function localDayNumber(unixSeconds, timeZone) {
  const { year, month, day } = localDateParts(unixSeconds, timeZone);
  return Date.UTC(year, month - 1, day) / 864e5;
}
function ageInDays(verifiedAt, now, timeZone) {
  return Math.max(0, localDayNumber(now, timeZone) - localDayNumber(verifiedAt, timeZone));
}
function freshnessFromAge(days) {
  if (days <= FRESHNESS_DAYS.verified) return "verified";
  if (days <= FRESHNESS_DAYS.dated) return "dated";
  if (days <= FRESHNESS_DAYS.stale) return "stale";
  return "expired";
}
function freshnessOf(verifiedAt, now, timeZone) {
  return freshnessFromAge(ageInDays(verifiedAt, now, timeZone));
}
function inRankings(freshness) {
  return freshness !== "expired";
}

// packages/domain/src/lead-messages.ts
var BADGE = 'Os pre\xE7os entram no site sem ningu\xE9m digitar nada. E marcando "sou dono ou gerente do bar", o {bar} ganha o selo azul de bar verificado ao lado do nome, como no Twitter e no Instagram: quem procura cerveja v\xEA que o bar reivindicou a p\xE1gina e que os pre\xE7os vieram do pr\xF3prio bar. \xC9 de gra\xE7a e n\xE3o muda a posi\xE7\xE3o no ranking, que \xE9 sempre pelo pre\xE7o.';
var LEAD_TEMPLATES = {
  message1: "Oi, pessoal do {bar}! Aqui \xE9 do Cerveja Barata (cervejabarata.com.br), o site que mostra onde a cerveja est\xE1 mais barata em {city}, com o pre\xE7o por litro para comparar lata, long neck, garrafa e chopp. {prices_sentence}\n\nMandem uma foto do card\xE1pio por este link: {menu_link}\n\n" + BADGE,
  followup: 'Oi, pessoal do {bar}! Passando de novo: o link para mandar a foto do card\xE1pio continua valendo: {menu_link}\n\nCom a foto, os pre\xE7os entram no site sem ningu\xE9m digitar nada, e marcando "sou dono ou gerente do bar" o {bar} ganha o selo azul de bar verificado. \xC9 de gra\xE7a e n\xE3o muda a posi\xE7\xE3o no ranking, que \xE9 sempre pelo pre\xE7o.',
  message2: 'Que bom ter o {bar} com a gente! Uma proposta para os primeiros bares:\n\nDestaque de fundador: um cart\xE3o "Patrocinado" no topo da {bairro_page}, um bar por p\xE1gina. O ranking n\xE3o muda: ningu\xE9m paga para subir. Junto vem, todo m\xEAs a partir de janeiro, o Raio-X de pre\xE7o do {bar}: quanto o seu litr\xE3o custa contra a m\xE9dia do bairro e quem est\xE1 mais barato perto de voc\xEA.\n\n\xC9 gr\xE1tis do lan\xE7amento (10/12) at\xE9 abrirmos o pagamento, em janeiro. Depois, R$ 250 por m\xEAs, sem fidelidade, e o pre\xE7o de fundador fica travado por 12 meses.\n\nEstamos testando tamb\xE9m ajuda com posts para o Instagram do bar. Se isso te interessa, me conta o que mais pesa na sua semana. Posso reservar a {bairro_page} para voc\xEAs? {founders_link}',
  offer_followup: "Oi, pessoal do {bar}! S\xF3 lembrando do destaque de bar fundador: gr\xE1tis at\xE9 janeiro, depois R$ 250 por m\xEAs, sem fidelidade, e o ranking continua pelo pre\xE7o. As vagas s\xE3o uma por p\xE1gina; para reservar: {founders_link}"
};

// packages/domain/src/leads.ts
var LEAD_STAGES = [
  "new",
  "dm1_sent",
  "replied",
  "menu_received",
  "offer_sent",
  "reserved",
  "paying",
  "declined",
  "no_response",
  "do_not_contact"
];
var CONTACT_RETENTION_DAYS = 90;
var PURGE_NOTE_PREFIX = "purged:";
var PURGE_NOTE = `${PURGE_NOTE_PREFIX} contact details deleted after ${CONTACT_RETENTION_DAYS} days without interaction`;
var MOVES = {
  // The first message from `new`; a follow-up while waiting (see leadTransition).
  send: { from: ["new", "dm1_sent", "offer_sent"] },
  reply: { from: ["new", "dm1_sent", "no_response"], to: "replied" },
  menu: { from: ["new", "dm1_sent", "replied", "no_response"], to: "menu_received" },
  badge: { from: ["new", "dm1_sent", "replied", "no_response"], to: "menu_received" },
  offer: { from: ["replied", "menu_received"], to: "offer_sent" },
  reserve: {
    from: ["new", "dm1_sent", "replied", "menu_received", "offer_sent", "no_response"],
    to: "reserved"
  },
  pay: { from: ["offer_sent", "reserved"], to: "paying" },
  decline: {
    from: ["new", "dm1_sent", "replied", "menu_received", "offer_sent", "reserved", "no_response"],
    to: "declined"
  },
  no_response: { from: ["dm1_sent", "offer_sent"], to: "no_response" },
  do_not_contact: {
    from: LEAD_STAGES.filter((stage) => stage !== "do_not_contact"),
    to: "do_not_contact"
  },
  note: { from: LEAD_STAGES }
};

// packages/domain/src/markets.ts
var MARKETS = {
  br: { id: "br", currency: "BRL", locale: "pt-BR", normMl: 1e3, normLabel: "L" },
  uk: { id: "uk", currency: "GBP", locale: "en-GB", normMl: 568.261, normLabel: "pint" }
};

// packages/domain/src/pages.ts
var BAIRRO_MIN_VENUES = 3;

// packages/domain/src/policy.ts
var perLitre = (hardMin, autoMin, autoMax, hardMax) => ({
  hardMin: hardMin * 100,
  autoMin: autoMin * 100,
  autoMax: autoMax * 100,
  hardMax: hardMax * 100
});
var DEFAULT_BOUNDS_BR = {
  lata: perLitre(6, 11, 40, 90),
  long_neck: perLitre(8, 17, 60, 130),
  garrafa: perLitre(6, 11, 40, 90),
  litrao: perLitre(5, 9, 30, 60),
  chopp: perLitre(8, 15, 80, 180),
  torre: perLitre(6, 12, 55, 120),
  pint: perLitre(8, 15, 80, 180),
  half_pint: perLitre(8, 15, 80, 180),
  other: perLitre(5, 9, 80, 180)
};
var RETAIL_BOUNDS_BR = {
  lata: perLitre(4, 5, 25, 60),
  long_neck: perLitre(5, 7, 35, 80),
  garrafa: perLitre(3.5, 4.5, 25, 60),
  litrao: perLitre(3, 4, 20, 50),
  chopp: perLitre(5, 8, 50, 120),
  torre: perLitre(5, 8, 50, 120),
  pint: perLitre(5, 8, 50, 120),
  half_pint: perLitre(5, 8, 50, 120),
  other: perLitre(3, 4, 50, 120)
};
var DISPUTE_FLAG = 2;
var DISPUTE_HIDE = 3;
function disputeState(openDisputes) {
  if (openDisputes >= DISPUTE_HIDE) return "hidden";
  if (openDisputes >= DISPUTE_FLAG) return "contested";
  return "ok";
}

// packages/domain/src/price.ts
function formatPpn(ppnMinor, market2) {
  return `${formatMoney(ppnMinor, market2)}/${market2.normLabel}`;
}

// packages/domain/src/verified.ts
var GENERIC_WORDS = new Set(
  "a o as os do da dos das de di du e the bar bares barzinho boteco botequim buteco restaurante lanchonete pub cardapio menu".split(
    " "
  )
);

// apps/site/src/city/copy.ts
var FORMAT_TYPE_LABELS = {
  "pt-BR": {
    lata: "Lata",
    long_neck: "Long neck",
    garrafa: "Garrafa",
    litrao: "Litr\xE3o",
    chopp: "Chopp",
    torre: "Torre",
    pint: "Pint",
    half_pint: "Meio pint"
  },
  en: {
    lata: "Can",
    long_neck: "Small bottle",
    garrafa: "600 ml bottle",
    litrao: "Litre bottle",
    chopp: "Draught",
    torre: "Beer tower",
    pint: "Pint",
    half_pint: "Half pint"
  }
};
var KIND_LABELS = {
  "pt-BR": {
    regular: "Pre\xE7o normal",
    happy_hour: "Happy hour",
    promo: "Promo\xE7\xE3o",
    combo: "Combo",
    open_bar: "Open bar"
  },
  en: {
    regular: "Regular price",
    happy_hour: "Happy hour",
    promo: "Promo",
    combo: "Combo",
    open_bar: "Open bar"
  }
};
var PACKAGING_LABELS = {
  "pt-BR": { balde: "Balde", torre: "Torre", combo: "Combo", dobro: "Em dobro" },
  en: { balde: "Bucket", torre: "Tower", combo: "Combo", dobro: "Two for one" }
};
function kindNote(kind, locale) {
  if (kind === "happy_hour") return locale === "en" ? " at happy hour" : " no happy hour";
  if (kind === "promo") return locale === "en" ? " on special offer" : " em promo\xE7\xE3o";
  return "";
}
var plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
var ARTICLES = {
  pituba: "na",
  barra: "na",
  graca: "na",
  federacao: "na",
  liberdade: "na",
  ribeira: "na",
  calcada: "na",
  "boca do rio": "na",
  saude: "na",
  vitoria: "na",
  lapa: "na",
  piedade: "na",
  "boca da mata": "na",
  "rio vermelho": "no",
  itaigara: "no",
  stiep: "no",
  comercio: "no",
  centro: "no",
  "centro historico": "no",
  canela: "no",
  "costa azul": "no",
  imbui: "no",
  cabula: "no",
  candeal: "no",
  garcia: "no",
  bonfim: "no",
  "horto florestal": "no",
  "caminho das arvores": "no",
  "santo antonio": "no",
  pelourinho: "no",
  "jardim armacao": "no",
  "chame-chame": "no",
  "chame chame": "no",
  "nordeste de amaralina": "no",
  "dique do tororo": "no",
  barris: "nos",
  mares: "nos"
};
function inPlace(name) {
  const key = name.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
  return `${ARTICLES[key] ?? "em"} ${name}`;
}
var COPY = {
  "pt-BR": {
    title: (city) => `Cerveja mais barata em ${city} hoje`,
    /** Happy hours only when the city has some: the description is what search engines and assistants quote. */
    description: (city, happyHours) => `Os pre\xE7os de cerveja mais baixos de ${city}, do mais barato ao mais caro por litro, com ${happyHours ? "happy hours, " : ""}bairros e mapa.`,
    heading: (city) => `A cerveja mais barata em ${city}`,
    categories: { bar: "Bares", mercado: "Mercados", deposito: "Dep\xF3sitos" },
    categoryHeading: {
      mercado: (city) => `A cerveja mais barata nos mercados de ${city}`,
      deposito: (city) => `A cerveja mais barata nos dep\xF3sitos de ${city}`
    },
    categoryTitle: {
      mercado: (city) => `Cerveja mais barata nos mercados de ${city} hoje`,
      deposito: (city) => `Cerveja mais barata nos dep\xF3sitos de ${city} hoje`
    },
    categoryDescription: {
      mercado: (city) => `Pre\xE7o da cerveja nos supermercados e mercadinhos de ${city}, das notas fiscais das \xFAltimas horas, em ordem de pre\xE7o por litro.`,
      deposito: (city) => `Pre\xE7o da cerveja nos dep\xF3sitos e distribuidoras de ${city}, das notas fiscais das \xFAltimas horas, em ordem de pre\xE7o por litro.`
    },
    categoryPlaces: { mercado: ["loja", "lojas"], deposito: ["dep\xF3sito", "dep\xF3sitos"] },
    categoryWhere: { mercado: "nos mercados", deposito: "nos dep\xF3sitos" },
    categoryNote: "Pre\xE7os de notas fiscais (NFC-e) das \xFAltimas 72 horas, do Pre\xE7o da Hora Bahia (SEFAZ-BA). Pre\xE7o de loja muda com cada promo\xE7\xE3o: sai do ranking depois de 14 dias. Venda proibida para menores de 18 anos.",
    summary: (s) => `Em ${s.date}, o menor pre\xE7o por litro em ${s.city}${s.recent ? " verificado nos \xFAltimos 90 dias" : ""} \xE9 ${s.perLitre}: ${s.beer}, ${s.serving}, por ${s.price}${kindNote(s.kind, "pt-BR")} \u2014 ${s.venue}. Comparamos ${plural(s.prices, "pre\xE7o", "pre\xE7os")} em ${plural(s.venues, ...s.places ?? ["bar", "bares"])}.`,
    summaryEmpty: (date, city) => `Em ${date}, ainda n\xE3o temos pre\xE7os publicados em ${city}. Os primeiros chegam em breve.`,
    anyBeer: "Cerveja",
    filters: "Filtros",
    when: "Quando",
    anyTime: "Qualquer hora",
    now: "Agora",
    pickTime: "Dia e hora",
    day: "Dia",
    hour: "Hora",
    where: "Onde",
    wholeCity: "Toda a cidade",
    near: "Perto de mim",
    radius: "Raio",
    bairro: "Bairro",
    mapArea: "\xC1rea do mapa",
    shopSource: "Fonte: nota fiscal no Pre\xE7o da Hora Bahia (SEFAZ-BA)",
    removeShop: "Sua loja? Tirar da lista",
    local: {
      question: "Onde voc\xEA est\xE1?",
      nearButton: "\u25CE Perto de mim",
      near: "perto de voc\xEA",
      pickBairro: "Escolher bairro",
      wholeCity: "a cidade toda",
      wholeCityButton: "Ver a cidade toda",
      showing: "Mostrando",
      change: "Trocar"
    },
    bairroHub: "Escolha o bairro",
    bairroHubIntro: "Os bairros com pre\xE7o, do litro mais barato ao mais caro.",
    bairroFrom: (price) => `a partir de ${price}`,
    format: "Formato",
    anyFormat: "Todos",
    brand: "Marca",
    anyBrand: "Todas",
    maxPrice: "Pre\xE7o m\xE1ximo",
    anyPrice: "Qualquer",
    kind: "Tipo",
    anyKind: "Todos",
    clear: "Limpar filtros",
    results: (n) => n === 1 ? "1 pre\xE7o" : `${n} pre\xE7os`,
    bars: (n) => n === 1 ? "1 bar" : `${n} bares`,
    morePrices: (n) => n === 1 ? "+1 pre\xE7o" : `+${n} pre\xE7os`,
    noResults: "Nenhum pre\xE7o com esses filtros.",
    locating: "Procurando voc\xEA\u2026",
    locationDenied: "N\xE3o deu para saber onde voc\xEA est\xE1. Escolha um bairro.",
    away: (km) => km < 1 ? `a ${Math.round(km * 1e3)} m` : `a ${km.toFixed(1).replace(".", ",")} km`,
    service: (pct) => `+${pct} % servi\xE7o`,
    couvert: (price) => `couvert ${price}`,
    confirmed: (n) => `confirmado por ${n} pessoas`,
    creditedTo: (nickname, level) => `enviado por @${nickname}${level ? ` \xB7 ${level}` : ""}`,
    disputed: "contestado",
    openBar: "Open bar",
    openBarIntro: "Bebe \xE0 vontade por um tempo: pre\xE7o do pacote, sem pre\xE7o por litro.",
    minutes: (n) => n % 60 === 0 ? `${n / 60} h` : `${n} min`,
    map: "Mapa dos bares com pre\xE7o",
    showMap: "Ver no mapa",
    openMap: "Abrir o mapa",
    centerOnMe: "Centralizar em mim",
    cheapestHere: (perLitre3) => `a partir de ${perLitre3}`,
    seePrices: "ver pre\xE7os",
    recent: "Pre\xE7os recentes",
    recentIntro: "Os \xFAltimos pre\xE7os conferidos, do mais novo ao mais antigo.",
    sendPrice: "Viu um pre\xE7o que n\xE3o est\xE1 aqui? Mande pra gente",
    greyPrompt: "Sabe o pre\xE7o? Conta pra gente",
    mapLegend: "Etiquetas: os 7 bares mais baratos por litro, com o pre\xE7o que voc\xEA paga. Pontos: os outros bares com pre\xE7o. No mapa aberto, os cinza ainda n\xE3o t\xEAm pre\xE7o (toque num para mandar).",
    cheapest: "O litro mais barato",
    cheapestIn: (place) => `A cerveja mais barata ${inPlace(place)}`,
    cheapestNear: (km) => `A cerveja mais barata a at\xE9 ${km} km de voc\xEA`,
    noPriceHereYet: "Ainda sem pre\xE7o por aqui. Viu um? Manda pra gente.",
    seeWholeCity: "Ver a cidade toda",
    seenOn: (date) => `visto em ${date}`,
    stillThatPrice: "Ainda \xE9 esse pre\xE7o?",
    mapLegendSponsored: "Pino na cor do topo da p\xE1gina: patrocinado.",
    sponsored: "Patrocinado",
    sponsoredCheapest: "Pre\xE7o mais baixo do bar",
    sponsoredHow: "Como funciona o patroc\xEDnio",
    freeSlot: "\xC9 dono de bar? Este espa\xE7o pode ser seu.",
    alcohol: "Com ou sem \xE1lcool",
    withAlcohol: "Com \xE1lcool",
    alcoholFree: "Sem \xE1lcool",
    alcoholFreeBadge: "0,0%",
    alcoholFreeLabel: "Cerveja sem \xE1lcool",
    alcoholFreeNote: "Cervejas sem \xE1lcool (0,0%), num ranking s\xF3 delas, do litro mais barato ao mais caro.",
    noAlcoholFree: "Ainda sem pre\xE7o de cerveja sem \xE1lcool por aqui. Viu um? Manda pra gente."
  },
  en: {
    title: (city) => `Cheapest beer in ${city} today`,
    description: (city, happyHours) => `The lowest beer prices in ${city}, ranked by price per litre, with ${happyHours ? "happy hours, " : ""}neighbourhoods and a map.`,
    heading: (city) => `The cheapest beer in ${city}`,
    categories: { bar: "Bars", mercado: "Supermarkets", deposito: "Dep\xF3sitos" },
    categoryHeading: {
      mercado: (city) => `The cheapest beer in ${city}'s supermarkets`,
      deposito: (city) => `The cheapest beer in ${city}'s dep\xF3sitos`
    },
    categoryTitle: {
      mercado: (city) => `Cheapest beer in ${city} supermarkets today`,
      deposito: (city) => `Cheapest beer in ${city} dep\xF3sitos today`
    },
    categoryDescription: {
      mercado: (city) => `Beer prices in ${city}'s supermarkets and corner shops, from the last hours' receipts, ranked by price per litre.`,
      deposito: (city) => `Beer prices in ${city}'s dep\xF3sitos (drink shops), from the last hours' receipts, ranked by price per litre.`
    },
    categoryPlaces: { mercado: ["shop", "shops"], deposito: ["dep\xF3sito", "dep\xF3sitos"] },
    categoryWhere: { mercado: "supermarkets", deposito: "dep\xF3sitos" },
    categoryNote: "Prices from electronic receipts (NFC-e) of the last 72 hours, via Bahia's Pre\xE7o da Hora (SEFAZ-BA). Shop prices change with every promotion: they leave the ranking after 14 days. No sales to under-18s.",
    summary: (s) => `On ${s.date}, the lowest price per litre in ${s.city}${s.recent ? " checked in the last 90 days" : ""} is ${s.perLitre}: ${s.beer}, ${s.serving}, for ${s.price}${kindNote(s.kind, "en")} \u2014 ${s.venue}. We compared ${plural(s.prices, "price", "prices")} at ${plural(s.venues, ...s.places ?? ["bar", "bars"])}.`,
    summaryEmpty: (date, city) => `As of ${date}, there are no published prices in ${city} yet. The first ones are coming soon.`,
    anyBeer: "Beer",
    filters: "Filters",
    when: "When",
    anyTime: "Any time",
    now: "Now",
    pickTime: "Day and time",
    day: "Day",
    hour: "Time",
    where: "Where",
    wholeCity: "Whole city",
    near: "Near me",
    radius: "Radius",
    bairro: "Neighbourhood",
    mapArea: "Map area",
    shopSource: "Source: electronic receipt, via Bahia's Pre\xE7o da Hora (SEFAZ-BA)",
    removeShop: "Your shop? Take it off the list",
    local: {
      question: "Where are you?",
      nearButton: "\u25CE Near me",
      near: "near you",
      pickBairro: "Pick a neighbourhood",
      wholeCity: "the whole city",
      wholeCityButton: "See the whole city",
      showing: "Showing",
      change: "Change"
    },
    bairroHub: "Pick a neighbourhood",
    bairroHubIntro: "Neighbourhoods with prices, from the cheapest litre to the dearest.",
    bairroFrom: (price) => `from ${price}`,
    format: "Format",
    anyFormat: "All",
    brand: "Brand",
    anyBrand: "All",
    maxPrice: "Max price",
    anyPrice: "Any",
    kind: "Type",
    anyKind: "All",
    clear: "Clear filters",
    results: (n) => n === 1 ? "1 price" : `${n} prices`,
    bars: (n) => n === 1 ? "1 bar" : `${n} bars`,
    morePrices: (n) => n === 1 ? "+1 price" : `+${n} prices`,
    noResults: "No prices match these filters.",
    locating: "Finding you\u2026",
    locationDenied: "We couldn't tell where you are. Pick a neighbourhood instead.",
    away: (km) => km < 1 ? `${Math.round(km * 1e3)} m away` : `${km.toFixed(1)} km away`,
    service: (pct) => `+${pct}% service`,
    couvert: (price) => `cover ${price}`,
    confirmed: (n) => `confirmed by ${n} people`,
    creditedTo: (nickname, level) => `sent by @${nickname}${level ? ` \xB7 ${level}` : ""}`,
    disputed: "disputed",
    openBar: "Open bar",
    openBarIntro: "All you can drink for a while: a package price, so no price per litre.",
    minutes: (n) => n % 60 === 0 ? `${n / 60} h` : `${n} min`,
    map: "Map of bars with prices",
    showMap: "Show on the map",
    openMap: "Open the map",
    centerOnMe: "Center on me",
    cheapestHere: (perLitre3) => `from ${perLitre3}`,
    seePrices: "see prices",
    recent: "Recent prices",
    recentIntro: "The latest prices checked, newest first.",
    sendPrice: "Seen a price that isn't here? Send it to us",
    greyPrompt: "Know the price? Tell us",
    mapLegend: "Tags: the 7 cheapest bars per litre, with the price you pay. Dots: the other bars with prices. On the open map, grey ones have no price yet (tap one to send it).",
    cheapest: "The cheapest litre",
    cheapestIn: (place) => `The cheapest beer in ${place}`,
    cheapestNear: (km) => `The cheapest beer within ${km} km of you`,
    noPriceHereYet: "No prices here yet. Seen one? Send it to us.",
    seeWholeCity: "See the whole city",
    seenOn: (date) => `seen on ${date}`,
    stillThatPrice: "Still this price?",
    mapLegendSponsored: "Pin in the colour of the page top: sponsored.",
    sponsored: "Sponsored",
    sponsoredCheapest: "The bar's lowest price",
    sponsoredHow: "How sponsorship works",
    freeSlot: "Own a bar? This spot could be yours.",
    alcohol: "With or without alcohol",
    withAlcohol: "With alcohol",
    alcoholFree: "Alcohol-free",
    alcoholFreeBadge: "0.0%",
    alcoholFreeLabel: "Alcohol-free beer",
    alcoholFreeNote: "Alcohol-free beers (0.0%), ranked on their own, cheapest litre first.",
    noAlcoholFree: "No alcohol-free beer prices here yet. Seen one? Send it to us."
  }
};

// apps/site/src/i18n/routes.ts
var ROUTES = {
  home: { "pt-BR": "/", en: "/en" },
  city: { "pt-BR": "/:city", en: "/en/:city" },
  cityShops: { "pt-BR": "/:city/mercados", en: "/en/:city/supermarkets" },
  cityDepots: { "pt-BR": "/:city/depositos", en: "/en/:city/depots" },
  neighbourhood: { "pt-BR": "/:city/:bairro", en: "/en/:city/:bairro" },
  happyHour: { "pt-BR": "/:city/happy-hour", en: "/en/:city/happy-hour" },
  happyHourDay: { "pt-BR": "/:city/happy-hour/:day", en: "/en/:city/happy-hour/:day" },
  openBar: { "pt-BR": "/:city/open-bar", en: "/en/:city/open-bar" },
  venue: { "pt-BR": "/:city/bares/:venue", en: "/en/:city/bars/:venue" },
  brand: { "pt-BR": "/:city/cerveja/:brand", en: "/en/:city/beer/:brand" },
  format: { "pt-BR": "/:city/formato/:format", en: "/en/:city/format/:format" },
  // Not "/en/:city/index": a last segment named index reads as a directory index (Cloudflare redirects it to "/").
  priceIndex: { "pt-BR": "/:city/indice", en: "/en/:city/price-index" },
  priceIndexMonth: { "pt-BR": "/:city/indice/:month", en: "/en/:city/price-index/:month" },
  newYearsEve: { "pt-BR": "/:city/reveillon", en: "/en/:city/new-years-eve" },
  carnival: { "pt-BR": "/:city/carnaval", en: "/en/:city/carnival" },
  data: { "pt-BR": "/dados", en: "/en/data" },
  developers: { "pt-BR": "/desenvolvedores", en: "/en/developers" },
  press: { "pt-BR": "/imprensa", en: "/en/press" },
  changelog: { "pt-BR": "/atualizacoes", en: "/en/changelog" },
  forBars: { "pt-BR": "/para-bares", en: "/en/for-bars" },
  forBarsFounders: { "pt-BR": "/para-bares/fundadores", en: "/en/for-bars/founders" },
  forBarsMenu: { "pt-BR": "/para-bares/cardapio", en: "/en/for-bars/menu" },
  submit: { "pt-BR": "/enviar", en: "/en/submit" },
  submitBar: { "pt-BR": "/enviar/bar", en: "/en/submit/bar" },
  about: { "pt-BR": "/sobre", en: "/en/about" },
  methodology: { "pt-BR": "/metodologia", en: "/en/methodology" },
  privacy: { "pt-BR": "/privacidade", en: "/en/privacy" },
  terms: { "pt-BR": "/termos", en: "/en/terms" },
  bot: { "pt-BR": "/bot", en: "/en/bot" },
  styleGuide: { "pt-BR": "/estilo" }
};
var DAY_SLUGS = {
  "pt-BR": ["domingo", "segunda", "terca", "quarta", "quinta", "sexta", "sabado"],
  en: ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
};
var patternFor = (key, locale) => ROUTES[key][locale];
function path(key, locale, params = {}) {
  const pattern = patternFor(key, locale);
  if (pattern === void 0) throw new Error(`route "${key}" has no ${locale} version`);
  return pattern.replace(/:([a-z]+)/g, (_, name) => {
    if (name === "day") {
      const slug = params.day === void 0 ? void 0 : DAY_SLUGS[locale][params.day];
      if (!slug) throw new Error(`route "${key}" needs a day from 0 to 6`);
      return slug;
    }
    const value = params[name];
    if (!value) throw new Error(`route "${key}" needs :${name}`);
    return encodeURIComponent(value);
  });
}
function fixedSegments() {
  const top = /* @__PURE__ */ new Set(["en", "api", "hooks", "mcp", "healthz", "_astro"]);
  const underCity = /* @__PURE__ */ new Set();
  for (const patterns of Object.values(ROUTES)) {
    for (const pattern of [patterns["pt-BR"], patterns.en]) {
      if (!pattern) continue;
      const segments = pattern.split("/").filter(Boolean);
      const [first, second] = segments[0] === "en" ? segments.slice(1) : segments;
      if (first && !first.startsWith(":")) top.add(first);
      if (first === ":city" && second && !second.startsWith(":")) underCity.add(second);
    }
  }
  return { top, underCity };
}
var RESERVED = fixedSegments();

// apps/site/src/city/deals.ts
var market = MARKETS.br;
function toCityDeal(row, venues, credits) {
  const venue = venues.get(row.venue_id);
  const credit = credits?.get(row.id);
  const value = (key) => row[key];
  const schedules = row.schedules ? JSON.parse(row.schedules) : [];
  return {
    id: row.id,
    venueId: row.venue_id,
    city: row.city,
    venueSlug: value("venue_slug"),
    venueName: value("venue_name"),
    bairro: value("neighbourhood"),
    bairroName: venue?.neighbourhood_name ?? null,
    category: dealCategory(venue?.kind, venue?.on_site ?? 1, value("on_site")),
    lat: value("lat"),
    lng: value("lng"),
    beerName: value("beer_name"),
    nonAlcoholic: Boolean(row.non_alcoholic),
    brand: value("brand"),
    brandName: value("brand_name"),
    formatCode: value("format_code"),
    formatType: value("format_type"),
    volumeMl: value("volume_ml"),
    units: value("units"),
    packaging: value("packaging"),
    priceMinor: value("price_minor"),
    ppnMinor: value("ppn_minor"),
    kind: value("kind"),
    openBarMinutes: value("open_bar_minutes"),
    conditions: value("conditions"),
    servicePct: value("service_charge_pct"),
    couvertMinor: value("couvert_minor"),
    windows: schedules.map(({ dow, start, end }) => ({ dow, startMin: start, endMin: end })),
    verifiedAt: value("last_verified_at"),
    confirmations: value("confirm_count") ?? 0,
    disputes: value("open_disputes") ?? 0,
    credit: credit ? { nickname: credit.nickname, approved: credit.approved_count } : null,
    venueVerifiedAt: venue?.verified_at ?? null
  };
}
var SHOP_PRICE_DAYS = 14;
function listable(deal, now, timeZone) {
  if (deal.verifiedAt === null || disputeState(deal.disputes) === "hidden") return false;
  if (deal.category !== "bar") return now - deal.verifiedAt <= SHOP_PRICE_DAYS * 86400;
  return inRankings(freshnessOf(deal.verifiedAt, now, timeZone));
}
function rankDeals(deals, now, timeZone, category = "bar", alcoholFree = false) {
  return deals.filter(
    (deal) => deal.category === category && deal.nonAlcoholic === alcoholFree && deal.kind !== "open_bar" && listable(deal, now, timeZone)
  ).sort(
    (a, b) => a.ppnMinor - b.ppnMinor || a.priceMinor - b.priceMinor || a.venueName.localeCompare(b.venueName, "pt-BR")
  );
}
function onePerBar(ranked) {
  const rows = /* @__PURE__ */ new Map();
  for (const deal of ranked) {
    const row = rows.get(deal.venueId);
    if (row) row.more += 1;
    else rows.set(deal.venueId, { ...deal, more: 0 });
  }
  return [...rows.values()];
}
function servingLabel(deal, locale) {
  const format2 = formatByCode(deal.formatCode);
  const name = format2 ? format2.label[locale] : `${FORMAT_TYPE_LABELS[locale][deal.formatType] ?? deal.formatType} ${deal.volumeMl} ml`;
  if (deal.packaging === "single" && deal.units === 1) return name;
  const packaging = PACKAGING_LABELS[locale][deal.packaging];
  const amount = deal.units > 1 ? `${deal.units} \xD7 ${name}` : name;
  return packaging ? `${packaging}: ${amount}` : amount;
}
function beerLabel(deal, locale) {
  return deal.beerName ?? deal.brandName ?? COPY[locale].anyBeer;
}
var money = (minor) => formatMoney(minor, market);
var perLitre2 = (minor) => formatPpn(minor, market);

// apps/site/src/city/venues.ts
var generatedAtOf = (data) => Math.floor(Date.parse(data.manifest.generated_at) / 1e3);

// apps/site/src/city/landing.ts
function cityRankings(data) {
  const generatedAt = generatedAtOf(data);
  const venues = new Map(data.venues.map((row) => [row.id, row]));
  return data.cities.map((city) => {
    const deals = data.deals.filter((row) => row.city === city.slug).map((row) => toCityDeal(row, venues));
    return {
      city,
      generatedAt,
      ranked: rankDeals(deals, generatedAt, city.timezone),
      alcoholFree: rankDeals(deals, generatedAt, city.timezone, "bar", true)
    };
  });
}
function bairroPages(rankings) {
  return rankings.flatMap((ranking2) => {
    const byBairro = /* @__PURE__ */ new Map();
    for (const deal of ranking2.ranked) {
      if (deal.bairro) byBairro.set(deal.bairro, [...byBairro.get(deal.bairro) ?? [], deal]);
    }
    return [...byBairro].filter(([, deals]) => new Set(deals.map((deal) => deal.venueId)).size >= BAIRRO_MIN_VENUES).map(([slug, deals]) => ({
      ...ranking2,
      ranked: deals,
      alcoholFree: (ranking2.alcoholFree ?? []).filter((deal) => deal.bairro === slug),
      bairro: { slug, name: deals[0]?.bairroName ?? slug }
    }));
  });
}
var venuesIn = (deals) => new Set(deals.map((deal) => deal.venueId)).size;

// tools/scripts/src/social/posts.ts
var POST_MIN_BARS = 5;
var TOP = 5;
var MAX_BAIRRO_POSTS = 4;
var SITE = "cervejabarata.com.br";
var INSTAGRAM = "@cervejabaratacombr";
var CONAR = "Beba com modera\xE7\xE3o \xB7 Se beber, n\xE3o dirija \xB7 Proibido para menores de 18 anos";
var flag = (value) => value === null || value === void 0 ? null : value ? 1 : 0;
function fromPublicDatasets({ updatedAt, city, deals, venues }) {
  return {
    manifest: { generated_at: updatedAt, source: "d1", environment: "production", views: {} },
    cities: [{ ...city, bbox: city.bbox ? JSON.stringify(city.bbox) : null }],
    venues: venues.map(
      (row) => ({
        ...row,
        city: city.slug,
        on_site: flag(row.on_site) ?? 1,
        attributes: JSON.stringify(row.attributes ?? [])
      })
    ),
    deals: deals.map(
      (row) => ({
        ...row,
        city: city.slug,
        on_site: flag(row.on_site),
        non_alcoholic: flag(row.non_alcoholic) ?? 0,
        schedules: JSON.stringify(row.schedules ?? [])
      })
    )
  };
}
function weekOf(at, timeZone) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date(at * 1e3)).map((part) => [part.type, part.value])
  );
  const local = new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day)));
  local.setUTCDate(local.getUTCDate() - (local.getUTCDay() + 6) % 7);
  return new Intl.DateTimeFormat("pt-BR", { day: "numeric", month: "long", timeZone: "UTC" }).format(local);
}
var plain = (text) => text.replace(/ /g, " ");
function entryOf(deal, rank) {
  const timed = deal.kind === "happy_hour" || deal.kind === "promo";
  return {
    rank,
    venue: deal.venueName,
    bairro: deal.bairroName,
    beer: beerLabel(deal, "pt-BR"),
    serving: servingLabel(deal, "pt-BR"),
    price: money(deal.priceMinor),
    perLitre: perLitre2(deal.ppnMinor),
    kind: timed ? KIND_LABELS["pt-BR"][deal.kind] ?? null : null,
    hours: timed && deal.windows.length > 0 ? describeWindows(deal.windows, "pt-BR") : null,
    deal
  };
}
function buildPosts(data, options) {
  const ranking2 = cityRankings(data).find((row) => row.city.slug === options.city);
  if (!ranking2) throw new Error(`unknown city: ${options.city}`);
  const { city, generatedAt } = ranking2;
  const week = weekOf(generatedAt, city.timezone);
  const updated = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeZone: city.timezone }).format(
    new Date(generatedAt * 1e3)
  );
  const pages = bairroPages([ranking2]).filter((page2) => venuesIn(page2.ranked) >= POST_MIN_BARS);
  let chosen = pages;
  if (options.bairros) {
    chosen = options.bairros.map((slug) => {
      const page2 = pages.find((candidate) => candidate.bairro.slug === slug);
      if (!page2) throw new Error(`${slug}: fewer than ${POST_MIN_BARS} bars with fresh prices`);
      return page2;
    });
  } else {
    chosen = [...pages].sort(
      (a, b) => venuesIn(b.ranked) - venuesIn(a.ranked) || a.bairro.name.localeCompare(b.bairro.name, "pt-BR")
    ).slice(0, options.maxBairros ?? MAX_BAIRRO_POSTS);
  }
  const post = (ranked, bairro) => {
    const place = bairro?.name ?? city.name;
    const route = bairro ? path("neighbourhood", "pt-BR", { city: city.slug, bairro: bairro.slug }) : path("city", "pt-BR", { city: city.slug });
    return {
      key: bairro?.slug ?? city.slug,
      city: { slug: city.slug, name: city.name },
      bairro,
      headline: COPY["pt-BR"].cheapestIn(place),
      placeLabel: bairro ? `${bairro.name}, ${city.name}` : city.name,
      url: `${SITE}${route}`,
      week,
      updated,
      bars: venuesIn(ranked),
      prices: ranked.length,
      top: onePerBar(ranked).slice(0, TOP).map((deal, index) => entryOf(deal, index + 1))
    };
  };
  const posts = [];
  if (venuesIn(ranking2.ranked) >= POST_MIN_BARS) posts.push(post(ranking2.ranked, null));
  for (const page2 of chosen) posts.push(post(page2.ranked, page2.bairro));
  return posts;
}
var hashtag = (name) => `#${name.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^A-Za-z0-9]+(.)?/g, (_, next) => (next ?? "").toUpperCase())}`;
function captionLine(entry, withBairro) {
  const where = withBairro && entry.bairro ? ` (${entry.bairro})` : "";
  const when = entry.deal.kind === "happy_hour" ? ` no happy hour${entry.hours ? ` (${entry.hours})` : ""}` : entry.deal.kind === "promo" ? ` em promo\xE7\xE3o${entry.hours ? ` (${entry.hours})` : ""}` : "";
  return `${entry.rank}. ${entry.venue}${where}: ${entry.beer}, ${entry.serving}, ${entry.price}${when} (${entry.perLitre})`;
}
function caption(post) {
  const first = post.top[0];
  if (!first) throw new Error(`${post.key}: no prices`);
  const tags = [
    hashtag(post.city.name),
    ...post.bairro ? [hashtag(post.bairro.name)] : [],
    "#cerveja",
    "#cervejabarata",
    "#happyhour",
    "#cervejagelada",
    "#boteco",
    "#bahia"
  ];
  return plain(
    [
      `${post.headline} nesta semana sai a ${first.perLitre.replace("/L", " o litro")}. Veja onde.`,
      [
        `Top 3 por pre\xE7o por litro, semana de ${post.week}:`,
        ...post.top.slice(0, 3).map((entry) => captionLine(entry, !post.bairro))
      ].join("\n"),
      "Pre\xE7o por litro para comparar lata, long neck, garrafa e chopp: quanto menor, mais cerveja pelo seu dinheiro. S\xF3 bares, um pre\xE7o por bar (o mais barato).",
      `O top ${post.top.length} est\xE1 no carrossel. O mapa completo, com ${post.bars} bares ${inPlace(post.bairro?.name ?? post.city.name)}, est\xE1 no link da bio: ${post.url}`,
      `Dados de ${post.updated}; os pre\xE7os podem mudar. Viu um pre\xE7o diferente? Avise pelo site.`,
      `\xC9 dono de bar? Mande o seu card\xE1pio: ${SITE}/para-bares`,
      CONAR,
      tags.join(" ")
    ].join("\n\n")
  );
}

// tools/scripts/src/social/slides.ts
var WIDTH = 1080;
var HEIGHT = 1350;
var COLORS = {
  primary: "#0b3954",
  deep: "#082c42",
  ink: "#0b2a3c",
  muted: "#4f6878",
  line: "#d8e4ea",
  accent: "#ff6f59",
  sea: "#bfe0ec",
  bg: "#f1f6f8",
  white: "#ffffff"
};
var html = (text) => text.replace(
  /[&<>"']/g,
  (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char] ?? char
);
function css(fonts2) {
  return `
@font-face { font-family: Rubik; font-weight: 300 900; src: url(data:font/woff2;base64,${fonts2.rubik}) format("woff2"); }
@font-face { font-family: Yellowtail; font-weight: 400; src: url(data:font/woff2;base64,${fonts2.yellowtail}) format("woff2"); }
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: ${WIDTH}px; height: ${HEIGHT}px; }
body { font-family: Rubik, sans-serif; color: ${COLORS.white}; background: ${COLORS.primary}; -webkit-font-smoothing: antialiased; }
.slide { position: relative; width: ${WIDTH}px; height: ${HEIGHT}px; overflow: hidden; display: flex; flex-direction: column; padding: 64px 72px 0; }
.top { display: flex; align-items: center; justify-content: space-between; }
.patch { display: inline-block; border-radius: 22px; padding: 10px 30px 18px; background: ${COLORS.accent}; color: ${COLORS.ink};
  box-shadow: inset 0 0 0 6px ${COLORS.accent}, inset 0 0 0 9px ${COLORS.ink}; font-family: Yellowtail, cursive; font-size: 58px; line-height: 1; }
.counter { font-weight: 700; font-size: 26px; letter-spacing: 0.12em; color: ${COLORS.sea}; }
.eyebrow { font-weight: 800; font-size: 30px; letter-spacing: 0.14em; text-transform: uppercase; color: ${COLORS.sea}; }
h1 { font-weight: 900; text-transform: uppercase; letter-spacing: -0.02em; line-height: 0.95; color: ${COLORS.accent}; text-wrap: balance; }
.main { flex: 1; min-height: 0; display: flex; flex-direction: column; justify-content: center; padding: 24px 0 32px; }
.foot { margin: 0 -72px; padding: 26px 72px 34px; background: ${COLORS.deep}; text-align: center; }
.foot .site { font-weight: 800; font-size: 34px; color: ${COLORS.white}; letter-spacing: 0.01em; }
.foot .conar { margin-top: 10px; font-weight: 500; font-size: 25px; color: ${COLORS.sea}; }
.card { background: ${COLORS.white}; color: ${COLORS.ink}; border-radius: 36px; }
.chip { display: inline-block; border-radius: 999px; padding: 8px 20px; font-weight: 800; font-size: 26px; letter-spacing: 0.04em; text-transform: uppercase; }
.chip.hh { background: ${COLORS.primary}; color: ${COLORS.white}; }
.nowrap { white-space: nowrap; }
/* Cover */
.cover h1 { font-size: 112px; margin-top: 18px; }
.cover .hero { margin-top: 52px; padding: 44px 52px 46px; position: relative; }
.cover .hero .label { font-weight: 800; font-size: 28px; letter-spacing: 0.12em; text-transform: uppercase; color: ${COLORS.muted}; }
.cover .hero .price { font-weight: 900; font-size: 168px; line-height: 1; letter-spacing: -0.03em; margin-top: 12px; color: ${COLORS.ink}; }
.cover .hero .litre { display: inline-block; margin-top: 18px; border-radius: 999px; background: ${COLORS.accent}; color: ${COLORS.ink}; padding: 10px 26px; font-weight: 900; font-size: 40px; }
.cover .hero .what { margin-top: 26px; font-weight: 700; font-size: 38px; line-height: 1.2; }
.cover .hero .where { margin-top: 6px; font-weight: 500; font-size: 32px; color: ${COLORS.muted}; }
.cover .hero .chip { position: absolute; top: 40px; right: 44px; }
.cover .swipe { margin-top: 40px; font-weight: 700; font-size: 32px; color: ${COLORS.sea}; text-align: right; }
/* Ranking */
.ranking h1 { font-size: 80px; margin-top: 12px; }
.ranking .sub { margin-top: 12px; font-weight: 500; font-size: 28px; color: ${COLORS.sea}; }
.ranking .list { margin-top: 26px; display: flex; flex-direction: column; gap: 14px; }
.row { display: flex; align-items: center; gap: 24px; padding: 20px 30px; min-height: 134px; }
.row .rank { flex: none; width: 74px; height: 74px; border-radius: 50%; background: ${COLORS.primary}; color: ${COLORS.white};
  display: flex; align-items: center; justify-content: center; font-weight: 900; font-size: 40px; }
.row.first .rank { background: ${COLORS.accent}; color: ${COLORS.ink}; }
.row .body { flex: 1; min-width: 0; }
.row .venue { font-weight: 800; font-size: 34px; line-height: 1.1; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.row .meta { margin-top: 6px; font-weight: 500; font-size: 26px; line-height: 1.25; color: ${COLORS.muted}; }
.row .chip { margin-top: 10px; font-size: 21px; padding: 6px 16px; }
.row .prices { flex: none; text-align: right; }
.row .price { font-weight: 900; font-size: 50px; line-height: 1; letter-spacing: -0.02em; white-space: nowrap; }
.row .litre { margin-top: 10px; display: inline-block; border-radius: 999px; background: ${COLORS.accent}; padding: 5px 16px; font-weight: 800; font-size: 25px; white-space: nowrap; }
/* Closing */
.closing h1 { font-size: 104px; }
.closing .url { margin-top: 44px; padding: 40px 48px; font-weight: 900; font-size: 54px; line-height: 1.15; word-break: break-word; }
.closing .url .path { color: ${COLORS.primary}; }
.closing .url .domain { color: ${COLORS.ink}; }
.closing .facts { margin-top: 40px; font-weight: 500; font-size: 34px; line-height: 1.4; color: ${COLORS.sea}; }
.closing .facts b { color: ${COLORS.white}; font-weight: 800; }
.closing .bars { margin-top: 44px; padding-top: 34px; border-top: 2px solid rgba(191, 224, 236, 0.35); font-size: 32px; line-height: 1.35; font-weight: 500; }
.closing .bars b { color: ${COLORS.accent}; font-weight: 800; }
`;
}
function page(fonts2, kind, index, total, body) {
  return `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><style>${css(fonts2)}</style></head><body>
<div class="slide ${kind}">
  <div class="top"><span class="patch">Cerveja Barata</span><span class="counter">${index}/${total}</span></div>
  <div class="main">${body}</div>
  <div class="foot"><div class="site">${SITE}</div><div class="conar">${html(CONAR)}</div></div>
</div></body></html>`;
}
var kindChip = (entry) => entry.kind ? `<span class="chip hh">${html(entry.kind)}${entry.hours ? ` \xB7 ${html(entry.hours)}` : ""}</span>` : "";
function cover(post) {
  const first = post.top[0];
  if (!first) throw new Error(`${post.key}: no prices`);
  return `<div class="eyebrow">Semana de ${html(post.week)}</div>
<h1>${html(post.headline)}</h1>
<div class="card hero">
  ${kindChip(first)}
  <div class="label">N\xBA 1 em pre\xE7o por litro</div>
  <div class="price">${html(first.price)}</div>
  <div class="litre">${html(first.perLitre.replace("/L", " o litro"))}</div>
  <div class="what">${html(first.beer)} \xB7 ${html(first.serving)}</div>
  <div class="where">${html(first.venue)}${first.bairro ? ` \xB7 ${html(first.bairro)}` : ""}</div>
</div>
<div class="swipe">Arraste para ver o top ${post.top.length} \u203A</div>`;
}
function ranking(post) {
  const rows = post.top.map((entry) => {
    const meta = [post.bairro ? null : entry.bairro, entry.beer, entry.serving].filter((part) => Boolean(part)).map((part) => `<span class="nowrap">${html(part)}</span>`).join(" \xB7 ");
    return `<div class="card row${entry.rank === 1 ? " first" : ""}">
  <div class="rank">${entry.rank}</div>
  <div class="body"><div class="venue">${html(entry.venue)}</div><div class="meta">${meta}</div>${kindChip(entry)}</div>
  <div class="prices"><div class="price">${html(entry.price)}</div><div class="litre">${html(entry.perLitre)}</div></div>
</div>`;
  }).join("\n");
  return `<div class="eyebrow">Semana de ${html(post.week)}</div>
<h1>Top ${post.top.length} ${html(inPlace(post.bairro?.name ?? post.city.name))}</h1>
<div class="sub">Pelo pre\xE7o por litro \xB7 s\xF3 bares \xB7 o pre\xE7o mais baixo de cada bar</div>
<div class="list">${rows}</div>`;
}
function closing(post) {
  const [domain, ...rest] = post.url.split("/");
  return `<h1>Veja o mapa completo</h1>
<div class="card url"><span class="domain">${html(domain ?? "")}</span><br><span class="path">/${html(rest.join("/"))}</span></div>
<div class="facts"><b>${post.bars} bares</b> com pre\xE7o ${html(inPlace(post.bairro?.name ?? post.city.name))}, na lista e no mapa, do mais barato ao mais caro por litro.<br>Dados de ${html(post.updated)}.</div>
<div class="bars">Siga <b>${INSTAGRAM}</b> para o ranking de toda semana.<br>\xC9 dono de bar? Mande o seu card\xE1pio: <b class="nowrap">${SITE}/para-bares</b></div>`;
}
var FIT_SCRIPT = `(() => {
  const main = document.querySelector(".main");
  const h1 = document.querySelector("h1");
  let size = parseFloat(getComputedStyle(h1).fontSize);
  while (main.scrollHeight > main.clientHeight + 1 && size > 56) {
    size -= 4;
    h1.style.fontSize = size + "px";
  }
  return main.scrollHeight <= main.clientHeight + 1;
})()`;
function slides(post, fonts2) {
  const bodies = [
    ["cover", cover(post)],
    ["ranking", ranking(post)],
    ["closing", closing(post)]
  ];
  return bodies.map(([kind, body], index) => page(fonts2, kind, index + 1, bodies.length, body));
}

// tools/scripts/src/social/upload.ts
var UPLOAD_TRIES = 3;
function batchName(kind, at, id) {
  if (kind === "manual")
    return `manual-${(id ?? String(at.getTime())).toLowerCase().replace(/[^a-z0-9-]/g, "")}`.slice(0, 48);
  const day = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Bahia",
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  }).format(at);
  return `week-${day}`;
}
async function uploadPost(post, target) {
  const fetcher = target.fetcher ?? fetch;
  const sleep = target.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  const url = `${target.api.replace(/\/+$/, "")}/api/admin/social/posts`;
  let failure = "";
  for (let attempt = 1; attempt <= UPLOAD_TRIES; attempt++) {
    try {
      const response = await fetcher(url, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${target.token}`,
          "user-agent": "cervejabarata-social/1.0"
        },
        body: JSON.stringify(post)
      });
      const text = await response.text();
      if (response.ok) return JSON.parse(text);
      failure = `HTTP ${response.status}: ${text.slice(0, 300)}`;
      if (response.status < 500) break;
    } catch (error) {
      failure = error.message;
    }
    if (attempt < UPLOAD_TRIES) await sleep(5e3 * attempt);
  }
  throw new Error(`${post.key}: upload failed (${failure})`);
}

// tools/scripts/src/social.ts
var BASE = "https://cervejabarata.com.br/api/v1";
async function fetchJson(url) {
  const response = await fetch(url, { headers: { "User-Agent": "cervejabarata-social/1.0" } });
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return await response.json();
}
async function liveData(city) {
  const [cities, deals, venues] = await Promise.all([
    fetchJson(`${BASE}/cities.json`),
    fetchJson(`${BASE}/${city}/deals.json`),
    fetchJson(`${BASE}/${city}/venues.json`)
  ]);
  const row = cities.items.find((item) => item.slug === city);
  if (!row) throw new Error(`unknown city: ${city}`);
  return fromPublicDatasets({
    updatedAt: deals.updated_at,
    city: row,
    deals: deals.items,
    venues: venues.items
  });
}
function fonts() {
  const require2 = createRequire(import.meta.url);
  const read = (file) => readFileSync2(require2.resolve(file)).toString("base64");
  return {
    rubik: read("@fontsource-variable/rubik/files/rubik-latin-wght-normal.woff2"),
    yellowtail: read("@fontsource/yellowtail/files/yellowtail-latin-400-normal.woff2")
  };
}
function uploadTarget() {
  const api = process.env.CB_API_URL;
  const token = process.env.CB_INGEST_TOKEN;
  if (!api || !token) throw new Error("--upload needs CB_API_URL and CB_INGEST_TOKEN");
  return { api, token };
}
async function main() {
  const { values } = parseArgs({
    options: {
      out: { type: "string" },
      city: { type: "string", default: "salvador" },
      bairros: { type: "string" },
      snapshot: { type: "string" },
      max: { type: "string" },
      upload: { type: "boolean", default: false },
      batch: { type: "string" }
    }
  });
  if (!values.out) throw new Error("--out <dir> is required");
  const target = values.upload ? uploadTarget() : null;
  const batch = values.batch ?? batchName("manual", /* @__PURE__ */ new Date());
  const city = values.city ?? "salvador";
  const data = values.snapshot ? readSnapshot(values.snapshot) : await liveData(city);
  const posts = buildPosts(data, {
    city,
    bairros: values.bairros?.split(",").map((slug) => slug.trim()),
    maxBairros: values.max ? Number(values.max) : void 0
  });
  mkdirSync(values.out, { recursive: true });
  const { chromium } = await import("@playwright/test");
  const browser = await chromium.launch();
  try {
    const page2 = await browser.newPage({ viewport: { width: WIDTH, height: HEIGHT }, deviceScaleFactor: 1 });
    const typefaces = fonts();
    for (const [index, post] of posts.entries()) {
      const stem = `post-${index + 1}-${post.key}`;
      const files = [];
      let fitted = true;
      for (const [slide, html2] of slides(post, typefaces).entries()) {
        await page2.setContent(html2, { waitUntil: "load" });
        await page2.evaluate("document.fonts.ready");
        const fits = await page2.evaluate(FIT_SCRIPT);
        const file = join2(values.out, `${stem}-${slide + 1}.jpg`);
        await page2.screenshot({ path: file, type: "jpeg", quality: 92 });
        files.push(file);
        if (!fits) {
          console.error(`${file}: the text doesn't fit; check it before posting`);
          process.exitCode = 1;
          fitted = false;
        }
      }
      const text = caption(post);
      writeFileSync(join2(values.out, `caption-${index + 1}-${post.key}.txt`), `${text}
`);
      const first = post.top[0];
      console.log(
        `${stem}: ${post.headline} \xB7 ${post.bars} bares \xB7 n\xBA 1 ${first?.price} (${first?.perLitre})`
      );
      if (target && fitted) {
        const result = await uploadPost(
          {
            batch,
            position: index + 1,
            key: post.key,
            title: post.headline,
            caption: text,
            slides: files.map((file) => readFileSync2(file).toString("base64"))
          },
          target
        );
        console.log(
          `${stem}: ${result.created ? "sent for review" : "already sent"} (post ${result.id}, ${result.status}, ${result.sent} admins)`
        );
      } else if (target) {
        console.error(`${stem}: not sent for review (a slide doesn't fit)`);
      }
    }
  } finally {
    await browser.close();
  }
}
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : error);
    process.exit(1);
  });
}
