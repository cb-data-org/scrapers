// Generated from tools/scripts/src/social.ts (cervejabarata.com.br): don't edit.

// tools/scripts/src/social.ts
import { mkdirSync as mkdirSync2, readFileSync as readFileSync3, writeFileSync as writeFileSync2 } from "node:fs";
import { createRequire } from "node:module";
import { join as join2 } from "node:path";
import { parseArgs } from "node:util";

// apps/site/src/data/snapshot.ts
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

// apps/site/src/data/ptax.ts
function readPtax(value) {
  const ptax = value;
  if (!ptax || typeof ptax.date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(ptax.date)) return null;
  const sell = Number(ptax.sell);
  return Number.isFinite(sell) && sell > 0.5 && sell < 50 ? { date: ptax.date, sell } : null;
}

// apps/site/src/data/snapshot.ts
var LICENSE = "ODbL-1.0";
var ATTRIBUTION = "Cerveja Barata (cervejabarata.com.br); receipt prices: Pre\xE7o da Hora Bahia, Bahia State Treasury (SEFAZ-BA); venues \xA9 OpenStreetMap contributors and Overture Maps Foundation";
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
    credits: existsSync(join(dir, "v_public_credits.json")) ? read("v_public_credits") : [],
    barFacts: existsSync(join(dir, "v_public_bar_facts.json")) ? read("v_public_bar_facts") : [],
    // The dollar rate, when the snapshot fetched one (remote builds): English pages show US$ with it.
    ptax: existsSync(join(dir, "ptax.json")) ? readPtax(read("ptax")) : null,
    // The Friday Top 10s the live site has published (remote builds read them back): this build adds to them.
    sextou: existsSync(join(dir, "sextou.json")) ? read("sextou") : null
  };
}
function withAlcoholFlag(deals, beers) {
  const free = new Set(beers.filter((beer) => beer.non_alcoholic).map((beer) => beer.slug));
  return deals.map(
    (row) => row.non_alcoholic === void 0 || row.non_alcoholic === null ? { ...row, non_alcoholic: free.has(row.beer) ? 1 : 0 } : row
  );
}

// tools/scripts/src/social/archive.ts
import { existsSync as existsSync2, mkdirSync, readFileSync as readFileSync2, writeFileSync } from "node:fs";
import { dirname } from "node:path";

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
  // Itaipava's tall can ("CERV LT ITAIPAVA PIL 550ML" on Salvador receipts, Oct 2026).
  format("lata", 550, "Lat\xE3o 550 ml", "Tall can 550 ml"),
  // The small long necks on Salvador receipts (Oct 2026): Império Gold and Coronita 210 ml, Heineken 250 ml.
  format("long_neck", 210, "Long neck 210 ml", "Bottle 210 ml"),
  format("long_neck", 250, "Long neck 250 ml", "Bottle 250 ml"),
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
  210: "long_neck",
  250: "long_neck",
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
  550: "lata",
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
var ALL_DAY = { "pt-BR": "24 horas", en: "24 hours" };
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
    const hours = window.startMin === 0 && window.endMin === DAY ? ALL_DAY[locale] : `${clock(window.startMin, locale)}\u2013${clock(window.endMin, locale)}`;
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
var TIMED_KINDS = ["happy_hour", "promo"];
function scheduleMissing(kind, windows) {
  return TIMED_KINDS.includes(kind ?? "") && (windows?.length ?? 0) === 0;
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
var SHOP_PRICE_DAYS = 14;
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
  const parts2 = formatter.formatToParts(new Date(unixSeconds * 1e3));
  const value = (type) => Number(parts2.find((part) => part.type === type)?.value);
  return { year: value("year"), month: value("month"), day: value("day") };
}
function localDayNumber(unixSeconds, timeZone) {
  const { year, month, day } = localDateParts(unixSeconds, timeZone);
  return Date.UTC(year, month - 1, day) / 864e5;
}
function localDate(unixSeconds, timeZone) {
  const { year, month, day } = localDateParts(unixSeconds, timeZone);
  const pad = (value) => String(value).padStart(2, "0");
  return `${year}-${pad(month)}-${pad(day)}`;
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
var LEAD_SIGNATURE = "Equipe Cerveja Barata";
var LEAD_CONAR_LINE = "Beba com modera\xE7\xE3o \xB7 Se beber, n\xE3o dirija \xB7 Proibido para menores de 18 anos";
var LEAD_BADGE = "o bar ganha de gra\xE7a o selo azul de verificado, como no Instagram.";
var LEAD_OPT_OUT_LINE = "Se n\xE3o quiserem receber mensagens, \xE9 s\xF3 avisar.";
var LEAD_CORRECTION_LINE = "Se algum pre\xE7o estiver errado, respondam aqui: a gente corrige ou tira o bar do site em at\xE9 24 horas.";
var LEAD_POSTS_PROBE = "Tamb\xE9m est\xE1 em teste uma ajuda com os posts do Instagram do bar. Se interessar, contem para a gente.";
var LEAD_TEMPLATES = {
  message1: `Oi, {bar}! Aqui \xE9 o Cerveja Barata (cervejabarata.com.br), que compara o pre\xE7o da cerveja nos bares de {city}. {prices_sentence}

Respondam RAIO-X e a gente manda, s\xF3 para voc\xEAs, {raiox_offer}.

Para p\xF4r todas as cervejas no site, mandem uma foto do card\xE1pio (n\xE3o pede senha nem pagamento): {menu_link}
A IA l\xEA os pre\xE7os, voc\xEAs s\xF3 conferem, e ${LEAD_BADGE}

${LEAD_CORRECTION_LINE}

` + LEAD_OPT_OUT_LINE,
  followup: "Oi, {bar}! Voltando ao Raio-X: respondam RAIO-X e a gente manda, s\xF3 para voc\xEAs, {raiox_offer}.\n\nO link da foto do card\xE1pio continua o mesmo (n\xE3o pede senha nem pagamento): {menu_link}\nUm detalhe: o selo de verificado vale por seis meses, se renova a cada card\xE1pio novo e n\xE3o mexe no ranking.\n\n" + LEAD_OPT_OUT_LINE,
  message2: 'Oi, {bar}! Obrigado pelo retorno.\n\nPara os primeiros bares, o Destaque de fundador: um cart\xE3o "Patrocinado" no topo da {bairro_page}, um bar por p\xE1gina, e a partir de janeiro de 2027 o Raio-X de pre\xE7o todo m\xEAs (o litro de voc\xEAs ao lado da m\xE9dia do bairro, no mesmo tamanho). Ningu\xE9m paga para subir no ranking.\n\nGr\xE1tis do lan\xE7amento (10/12/2026) at\xE9 31/01/2027. Nada \xE9 cobrado sem um novo sim de voc\xEAs: em janeiro, a gente pergunta se querem continuar a partir de fevereiro, a R$ 250 por m\xEAs, no Pix, sem fidelidade e com o pre\xE7o travado por 12 meses. Se n\xE3o, o cart\xE3o sai e a p\xE1gina do bar fica igual.\n\nA gente reserva a vaga? \xC9 s\xF3 responder "sim" (ou usar o link: {founders_link}).\n\n' + LEAD_OPT_OUT_LINE,
  offer_followup: `Oi, {bar}! Voltando ao destaque de fundador no topo da {bairro_page}: gr\xE1tis at\xE9 31/01/2027, e nada \xE9 cobrado sem um novo sim de voc\xEAs. A partir de fevereiro, s\xF3 se quiserem, R$ 250 por m\xEAs, no Pix, sem fidelidade. O ranking n\xE3o muda, e \xE9 um bar por p\xE1gina. Para reservar, \xE9 s\xF3 responder "sim" aqui ou usar o link: {founders_link}

${LEAD_POSTS_PROBE}

` + LEAD_OPT_OUT_LINE
};
var LEAD_SLOT_TAKEN_TEMPLATE = "Oi, {bar}! Obrigado pelo retorno. O destaque da {bairro_page} j\xE1 foi reservado, e \xE9 um bar por p\xE1gina. A gente est\xE1 preparando outras formas de ajudar o bar, como o Raio-X de pre\xE7o mensal. Querem que a gente avise quando estiver pronto?\n\n" + LEAD_OPT_OUT_LINE;

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
  note: { from: LEAD_STAGES },
  // The owner's way back for a lead taken out of the campaign (declined) though the bar never refused; an opt-out
  // (do_not_contact) stays final.
  reopen: { from: ["declined"], to: "new" }
};

// packages/domain/src/markets.ts
var MARKETS = {
  br: { id: "br", currency: "BRL", locale: "pt-BR", normMl: 1e3, normLabel: "L" },
  uk: { id: "uk", currency: "GBP", locale: "en-GB", normMl: 568.261, normLabel: "pint" }
};

// packages/domain/src/names.ts
var NAME_OVERRIDES = {
  // The receipt split "BANDII'S" in two; the slug kept the apostrophe's place.
  "salvador/bandii-s-espetaria-beer": "Bandii's Espetaria Beer"
};
var TRADE_NAMES = {
  // Sendas Distribuidora S.A. runs the Assaí Atacadista stores.
  "sendas distribuidora": "Assa\xED Atacadista",
  "atakarejo distribuidor de alimentos e bebidas": "Atakarejo",
  "atakarejo dist de alim e beb": "Atakarejo",
  "hiperideal empreendimentos": "Hiperideal",
  "g barbosa": "GBarbosa",
  "g. barbosa": "GBarbosa",
  gbarbosa: "GBarbosa",
  cinepolis: "Cin\xE9polis",
  "cinepolis operadora de cinemas do brasil": "Cin\xE9polis",
  "cinemark brasil": "Cinemark",
  "centro de atividades sesc casa do comercio": "Sesc Casa do Com\xE9rcio",
  // "Com. de Bebidas e Prod. Alimentícios", cut short on the receipt.
  "varanda do horto com de bebidas e prod aliment": "Varanda do Horto"
};
var ACCENTED_WORDS = {
  acai: "a\xE7a\xED",
  acais: "a\xE7a\xEDs",
  acaraje: "acaraj\xE9",
  acougue: "a\xE7ougue",
  acougues: "a\xE7ougues",
  agua: "\xE1gua",
  aguas: "\xE1guas",
  alem: "al\xE9m",
  alianca: "alian\xE7a",
  alimenticio: "aliment\xEDcio",
  alimenticios: "aliment\xEDcios",
  armacao: "arma\xE7\xE3o",
  armazem: "armaz\xE9m",
  assai: "assa\xED",
  assuncao: "assun\xE7\xE3o",
  atacadao: "atacad\xE3o",
  atakadao: "atakad\xE3o",
  atlantico: "atl\xE2ntico",
  baratao: "barat\xE3o",
  barreirao: "barreir\xE3o",
  bompreco: "bompre\xE7o",
  brandao: "brand\xE3o",
  cafe: "caf\xE9",
  calabetao: "calabet\xE3o",
  calcada: "cal\xE7ada",
  camacari: "cama\xE7ari",
  camarao: "camar\xE3o",
  camaroes: "camar\xF5es",
  chale: "chal\xE9",
  comercio: "com\xE9rcio",
  conceicao: "concei\xE7\xE3o",
  construcao: "constru\xE7\xE3o",
  conveniencia: "conveni\xEAncia",
  conveniencias: "conveni\xEAncias",
  culinaria: "culin\xE1ria",
  delicias: "del\xEDcias",
  deposito: "dep\xF3sito",
  depositos: "dep\xF3sitos",
  economica: "econ\xF4mica",
  economico: "econ\xF4mico",
  emporio: "emp\xF3rio",
  espaco: "espa\xE7o",
  esperanca: "esperan\xE7a",
  estacao: "esta\xE7\xE3o",
  familia: "fam\xEDlia",
  federacao: "federa\xE7\xE3o",
  feirao: "feir\xE3o",
  galvao: "galv\xE3o",
  generos: "g\xEAneros",
  graca: "gra\xE7a",
  helio: "h\xE9lio",
  hortela: "hortel\xE3",
  imbui: "imbu\xED",
  imperio: "imp\xE9rio",
  industria: "ind\xFAstria",
  irmaos: "irm\xE3os",
  irmas: "irm\xE3s",
  itapua: "itapu\xE3",
  joao: "jo\xE3o",
  jose: "jos\xE9",
  lider: "l\xEDder",
  macaubas: "maca\xFAbas",
  mae: "m\xE3e",
  mercadao: "mercad\xE3o",
  musica: "m\xFAsica",
  nazare: "nazar\xE9",
  opcao: "op\xE7\xE3o",
  panificacao: "panifica\xE7\xE3o",
  pao: "p\xE3o",
  paraiba: "para\xEDba",
  paraiso: "para\xEDso",
  pernambues: "pernambu\xE9s",
  perseveranca: "perseveran\xE7a",
  piata: "piat\xE3",
  piaui: "piau\xED",
  piraja: "piraj\xE1",
  pituacu: "pitua\xE7u",
  portao: "port\xE3o",
  praca: "pra\xE7a",
  preco: "pre\xE7o",
  precos: "pre\xE7os",
  raizes: "ra\xEDzes",
  recomecar: "recome\xE7ar",
  refeicoes: "refei\xE7\xF5es",
  sacolao: "sacol\xE3o",
  sao: "s\xE3o",
  sebastiao: "sebasti\xE3o",
  sensacao: "sensa\xE7\xE3o",
  sertao: "sert\xE3o",
  servico: "servi\xE7o",
  servicos: "servi\xE7os",
  simoes: "sim\xF5es",
  tenis: "t\xEAnis",
  tororo: "toror\xF3",
  uniao: "uni\xE3o",
  util: "\xFAtil",
  varejao: "varej\xE3o",
  verdurao: "verdur\xE3o",
  vitoria: "vit\xF3ria",
  ze: "z\xE9"
};
var ACCENTED_PHRASES = [
  ["cafe da manha", "caf\xE9 da manh\xE3"],
  ["da roca", "da ro\xE7a"],
  ["mil paes", "mil p\xE3es"],
  ["por do sol", "p\xF4r do sol"],
  ["santo antonio", "santo ant\xF4nio"],
  ["sao luis", "s\xE3o lu\xEDs"]
];
var SMALL_WORDS = new Set("ao aos \xE0 \xE0s de da do das dos e em na no nas nos com por para pra".split(" "));
var KEEP_CASE_WORDS = new Set(
  "a o as os um uma and by to of the for at on in with di del della la le du dans des et".split(" ")
);
var NOT_ACRONYMS = new Set("mr mrs sr sra dr dra jr st km kg ml mkt".split(" "));
var UNITS = new Set("h hs hr hrs km kg ml l g mg".split(" "));
var LEGAL_SUFFIX = /(?:[\s,]+|^)(?:ltda|limitada|me|epp|eireli|s\/a|s\.a\.?)\.?\s*$/i;
var DESCRIPTORS = [
  /[\s,-]+comercio (?:atacadista|varejista)\b.*$/,
  /[\s,-]+(?:comercio|comercial) de (?:alimentos|generos alimenticios|produtos alimenticios|mercadorias)\b.*$/,
  /[\s,-]+comercio e (?:industria|servicos)\b.*$/,
  /[\s,-]+ind\.? e com\.?$/,
  /[\s,-]+distribuidora? de alimentos e bebidas$/,
  /[\s,-]+servicos alimenticios$/,
  /[\s,-]+operadora de cinemas do brasil$/,
  /[\s,-]+empreendimentos$/,
  /[\s,-]+(?:comercio|comercial)$/
];
var LETTER = new RegExp("\\p{L}", "u");
var ROMAN = /^(?:i{2,3}|iv|vi{1,3}|ix|xi{1,3}|xiv|xv)$/;
var ASCII = /^[\x20-\x7e]*$/;
function foldChar(char) {
  if (char.length !== 1) return char;
  const base = char.normalize("NFD").charAt(0).toLowerCase();
  return base.length === 1 ? base : char;
}
function foldName(text) {
  return [...text].map(foldChar).join("");
}
var plain = (text) => foldName(text).replace(/\s+/g, " ").trim();
var hasVowel = (text) => /[aeiouy]/.test(foldName(text));
function meaningful(text) {
  const words = text.trim().split(/\s+/);
  const last = words.at(-1)?.toLowerCase() ?? "";
  return !SMALL_WORDS.has(last) && words.some((word) => new RegExp("\\p{L}{3,}", "u").test(word) && hasVowel(word) && !SMALL_WORDS.has(word.toLowerCase()));
}
function withoutLegalParts(name) {
  let text = name.normalize("NFC").replace(/\s+/g, " ").trim().replace(/^fantasia\s*:\s*/i, "").replace(/\s*['"‘’“”]*\s*em\s+recupera[çc][ãa]o\s+judicial\s*['"‘’“”]*\s*/gi, " ").replace(/\s+-\s+filial\s+\d+\s*$/i, "").trim();
  for (let previous = ""; previous !== text; ) {
    previous = text;
    text = text.replace(LEGAL_SUFFIX, "").trim();
  }
  const folded = foldName(text);
  for (const descriptor of DESCRIPTORS) {
    const match = descriptor.exec(folded);
    if (match && meaningful(text.slice(0, match.index))) {
      text = text.slice(0, match.index).trim();
      break;
    }
  }
  return text.replace(/(?:\s+(?:e|&|de|da|do|-))+$/i, "").trim();
}
function inCaseOf(source, target) {
  const letters = [...target];
  return [...source].map((char, index) => {
    const fix = letters[index] ?? char;
    return char === char.toUpperCase() && char !== char.toLowerCase() ? fix.toUpperCase() : fix;
  }).join("");
}
function accented(word) {
  if (!ASCII.test(word)) return word;
  const right = ACCENTED_WORDS[word.toLowerCase()];
  return right && right.length === word.length ? inCaseOf(word, right) : word;
}
function phraseAccents(name) {
  const words = name.split(" ");
  const folded = words.map(foldName);
  for (const [phrase, right] of ACCENTED_PHRASES) {
    const wanted = phrase.split(" ");
    const fixes = right.split(" ");
    for (let start = 0; start + wanted.length <= words.length; start += 1) {
      if (!wanted.every((word, offset) => folded[start + offset] === word)) continue;
      wanted.forEach((_, offset) => {
        const word = words[start + offset] ?? "";
        const fix = fixes[offset] ?? word;
        if (ASCII.test(word) && fix.length === word.length) words[start + offset] = inCaseOf(word, fix);
      });
    }
  }
  return words.join(" ");
}
function titleWord(core, keepCase, last) {
  const letters = core.replace(/[^\p{L}]/gu, "");
  if (!letters) return core;
  if (new RegExp("^(?:\\p{L}\\.)+\\p{L}?\\.?$", "u").test(core)) return core.toUpperCase();
  if (new RegExp("^\\p{L}&\\p{L}$", "u").test(core)) return core.toUpperCase();
  if (keepCase && core !== core.toLowerCase() && core.slice(1) !== core.slice(1).toLowerCase()) return core;
  const lower = core.toLowerCase();
  if (last && ROMAN.test(lower)) return core.toUpperCase();
  if (letters.length >= 2 && letters.length <= 4 && !hasVowel(letters) && !NOT_ACRONYMS.has(letters.toLowerCase()))
    return core.toUpperCase();
  const code = new RegExp("^\\d+(\\p{L}{1,3})\\d*$", "u").exec(core)?.[1];
  if (code) return hasVowel(code) || UNITS.has(code.toLowerCase()) ? lower : core.toUpperCase();
  return lower.split("-").map(
    (part) => part.replace(
      new RegExp("^(\\p{L}')?(\\p{L})", "u"),
      (_, elided, first) => `${elided?.toUpperCase() ?? ""}${first.toUpperCase()}`
    )
  ).join("-");
}
function titleCase(name) {
  const keepCase = new RegExp("\\p{Ll}", "u").test(name);
  const tokens = name.split(" ").filter(Boolean);
  const out = [];
  let first = true;
  tokens.forEach((token, index) => {
    if (!LETTER.test(token) && !/\d/.test(token)) {
      out.push(token);
      if (token === "-" || token.endsWith(":")) first = true;
      return;
    }
    const [, lead = "", core = "", tail = ""] = /^([^\p{L}\d@+]*)(.*?)([^\p{L}\d.'&]*)$/u.exec(token) ?? [];
    const previous = tokens[index - 1] ?? "";
    const lower = core.toLowerCase();
    let word;
    if (keepCase && core.length > 1 && core === core.toUpperCase() && core !== lower) {
      word = core;
    } else if (new RegExp("^\\p{L}$", "u").test(core)) {
      const initial = !["a", "o", "e"].includes(lower) || new RegExp("^\\p{L}$", "u").test(previous) || previous === "&";
      if (first || tail.startsWith("+") || initial && !(lower === "e" && previous !== "&"))
        word = core.toUpperCase();
      else if (lower === "e" || !keepCase) word = lower;
      else word = core;
    } else if (!first && SMALL_WORDS.has(lower)) {
      word = lower;
    } else if (!first && KEEP_CASE_WORDS.has(lower)) {
      word = keepCase ? core : lower;
    } else {
      const possessive = new RegExp("^(.*\\p{L})('s)$", "iu").exec(core);
      const last = index === tokens.length - 1;
      word = possessive ? `${titleWord(possessive[1] ?? "", keepCase, last)}${(possessive[2] ?? "").toLowerCase()}` : titleWord(core, keepCase, last);
    }
    out.push(`${lead}${word.split("-").map(accented).join("-")}${tail}`);
    first = token.endsWith(":");
  });
  return out.join(" ");
}
var cache = /* @__PURE__ */ new Map();
function venueDisplayName(name, venue) {
  const override = venue ? NAME_OVERRIDES[`${venue.city}/${venue.slug}`] : void 0;
  if (override) return override;
  const known = cache.get(name);
  if (known !== void 0) return known;
  const cleaned = withoutLegalParts(name);
  const trade = TRADE_NAMES[plain(cleaned)] ?? TRADE_NAMES[plain(name)];
  const styled = !cleaned.includes(" ") && !new RegExp("\\p{Ll}", "u").test(cleaned);
  const display = trade ?? ((styled ? accented(cleaned) : titleCase(phraseAccents(cleaned))) || name.trim());
  cache.set(name, display);
  return display;
}

// packages/domain/src/opening-hours.ts
var DAY2 = "(?:Mo|Tu|We|Th|Fr|Sa|Su)";
var DAY_RANGE = `${DAY2}(?:-${DAY2})?`;
var TIME = "(?:(?:[01]\\d|2[0-3]):[0-5]\\d|24:00)";
var SPAN = `${TIME}-${TIME}`;
var list = (item) => `${item}(?:\\s*,\\s*${item})*`;
var RULE = new RegExp(`^(?:(${list(DAY_RANGE)})\\s+)?(${list(SPAN)}|off|closed)$`);

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

// packages/domain/src/whatsapp-copy.ts
var WA_FOOTER = `${LEAD_SIGNATURE}
${LEAD_CONAR_LINE}`;
var waWithFooter = (body) => `${body}

${WA_FOOTER}`;
var WA_REPLY_WITHIN = "em at\xE9 24 horas";
var WA_DISCLOSURE = `As respostas aqui s\xE3o autom\xE1ticas. Para falar com uma pessoa, escrevam PESSOA: ela responde ${WA_REPLY_WITHIN}.`;
var WA_FIRST_KNOWN = [
  "Oi, {bar}! Aqui \xE9 o Cerveja Barata ({site}), o site que compara o pre\xE7o da cerveja nos bares.",
  WA_DISCLOSURE
].join("\n");
var GREETING_UNKNOWN = "Oi! Aqui \xE9 o Cerveja Barata ({site}).";
var ASK_BAR = "Para a gente achar o bar certo, qual \xE9 o nome dele e o bairro?";
var WA_FIRST_UNKNOWN = waWithFooter([GREETING_UNKNOWN, WA_DISCLOSURE, ASK_BAR].join("\n"));
function waFirstUnknownAnswer(answer, faqs = []) {
  return waWithFooter(
    [
      faqs.includes("quem") ? null : GREETING_UNKNOWN,
      answer,
      faqs.includes("robo") ? ASK_BAR : `${WA_DISCLOSURE}
${ASK_BAR}`
    ].filter((part) => part !== null).join("\n\n")
  );
}
var WA_FIRST_UNKNOWN_ANSWER = waFirstUnknownAnswer("{answer}");
var WA_ASK_BAR_AGAIN = [
  "A gente n\xE3o encontrou esse bar na nossa lista.",
  "Podem mandar o nome como est\xE1 na fachada e o bairro, ou o perfil do bar no Instagram?",
  "Se preferirem, escrevam PESSOA e uma pessoa ajuda."
].join("\n");
var WA_AFTER_RAIOX = [
  "Para atualizar ou completar os pre\xE7os de voc\xEAs no site, mandem a foto do card\xE1pio aqui mesmo.",
  "Se tiverem d\xFAvidas, \xE9 s\xF3 perguntar."
].join("\n");
var WA_NO_RAIOX = [
  "Ainda n\xE3o h\xE1 pre\xE7os suficientes no site para montar o Raio-X de voc\xEAs.",
  "Mandem aqui uma foto do card\xE1pio: quando os pre\xE7os entrarem no site, \xE9 s\xF3 pedir RAIO-X de novo."
].join("\n");
var WA_MENU_THANKS = [
  "Obrigado, {bar}! A foto do card\xE1pio chegou.",
  "A gente l\xEA os pre\xE7os e, depois de uma revis\xE3o, eles entram na p\xE1gina de voc\xEAs no site."
].join("\n");
var WA_ASK_PHOTO_BAR = [
  "Obrigado pela foto! De qual bar \xE9, e em que bairro fica?",
  "Assim os pre\xE7os v\xE3o para a p\xE1gina certa."
].join("\n");
var WA_MENU_READBACK_TAIL = [
  "Se os pre\xE7os estiverem certos, respondam SIM.",
  "Se algum estiver errado, escrevam o item e o pre\xE7o certo."
].join("\n");
var WA_CORRECTION_ACK = [
  "Entendido, {bar}. Uma pessoa j\xE1 foi avisada.",
  "A gente corrige o pre\xE7o, ou tira o bar do site, em at\xE9 24 horas.",
  "Se tiverem o pre\xE7o certo, \xE9 s\xF3 mandar aqui."
].join("\n");
var WA_OPTOUT_CONFIRM = [
  "Pronto: a gente n\xE3o escreve mais para este n\xFAmero.",
  "Se tamb\xE9m quiserem tirar o bar do site, \xE9 s\xF3 pedir por aqui: a gente tira em at\xE9 24 horas."
].join("\n");
var WA_HUMAN = [
  `Combinado: uma pessoa responde neste mesmo chat ${WA_REPLY_WITHIN}.`,
  "Se quiserem, j\xE1 podem deixar a pergunta aqui: assim a resposta vem completa."
].join("\n");
var WA_AUDIO_UNCLEAR = [
  "A gente n\xE3o conseguiu entender o \xE1udio.",
  "Podem escrever a mensagem?"
].join("\n");
var WA_PLACEHOLDER = `A mensagem chegou. Uma pessoa responde aqui mesmo ${WA_REPLY_WITHIN}.`;
var WA_PLACEHOLDER_FIRST = waWithFooter(
  ["Oi! Aqui \xE9 o Cerveja Barata ({site}). Esta \xE9 uma resposta autom\xE1tica.", WA_PLACEHOLDER].join("\n")
);
var WA_MONEY_HOLD = [
  `Sobre valores e condi\xE7\xF5es, quem responde \xE9 uma pessoa, aqui mesmo, ${WA_REPLY_WITHIN}.`,
  "Nada \xE9 cobrado sem um sim de voc\xEAs."
].join("\n");
var WA_INTEREST_HOLD = `Que bom! Uma pessoa j\xE1 foi avisada e responde aqui mesmo, ${WA_REPLY_WITHIN}.`;
var WA_OTHER = `Essa pergunta fica com uma pessoa, que responde aqui mesmo ${WA_REPLY_WITHIN}.`;
var WA_CONSUMER = [
  "Oi, obrigado por escrever! Aqui a gente conversa com donos de bar; se for o seu caso, \xE9 s\xF3 mandar RAIO-X.",
  "Para ver o pre\xE7o da cerveja perto de voc\xEA, do mais barato ao mais caro: {site}",
  LEAD_CONAR_LINE
].join("\n");
var WA_NOT_NOW = [
  "Sem problema, {bar}! A gente n\xE3o vai insistir por aqui.",
  "Quando quiserem, o Raio-X sai a qualquer hora: \xE9 s\xF3 mandar RAIO-X."
].join("\n");
var WA_DECLINE_CLOSE = [
  "Tudo bem, {bar}, obrigado pelo retorno. A gente n\xE3o vai insistir.",
  "O bar continua no site, de gra\xE7a. Se quiserem corrigir um pre\xE7o ou tirar o bar, \xE9 s\xF3 avisar aqui."
].join("\n");
var WA_RESERVE_HOLD = [
  `Obrigado, {bar}! Uma pessoa responde aqui, ${WA_REPLY_WITHIN}, para confirmar a reserva.`,
  "Ela tamb\xE9m manda o resumo do que foi combinado."
].join("\n");
var WA_RESERVE_CONFIRMED = waWithFooter(
  [
    'Pronto, {bar}: a vaga est\xE1 reservada! O Destaque de fundador \xE9 um cart\xE3o "Patrocinado" no topo da {bairro_page}, um bar por p\xE1gina.',
    "O destaque inclui o Raio-X de pre\xE7o todo m\xEAs, a partir de janeiro de 2027: o litro de voc\xEAs ao lado da m\xE9dia do bairro, no mesmo tamanho.",
    "\xC9 gr\xE1tis do lan\xE7amento (10/12/2026) at\xE9 31/01/2027, e nada \xE9 cobrado sem um novo sim de voc\xEAs.",
    "Em janeiro, a gente pergunta se querem continuar a partir de fevereiro, a R$ 250 por m\xEAs, no Pix, sem fidelidade e com o pre\xE7o travado por 12 meses.",
    "Se n\xE3o quiserem, o cart\xE3o sai e a p\xE1gina do bar fica igual. O ranking n\xE3o muda: ningu\xE9m paga para subir."
  ].join("\n")
);
var WA_FAQ = {
  /** Who are you, do you have a CNPJ. Plain: there is none. Today-true; see EXPIRES. */
  quem: [
    "O Cerveja Barata ({site}) \xE9 um site novo que compara o pre\xE7o da cerveja nos bares.",
    "A gente ainda n\xE3o tem CNPJ, mas nada \xE9 cobrado sem um sim de voc\xEAs.",
    "Se preferirem falar com uma pessoa, escrevam PESSOA."
  ].join("\n"),
  /** Robot or person. The money half answers "dinheiro eu só trato com gente". */
  robo: [
    "Aqui responde um assistente autom\xE1tico, n\xE3o uma pessoa.",
    `Para falar com uma pessoa, escrevam PESSOA: ela responde ${WA_REPLY_WITHIN}.`,
    "Valores e condi\xE7\xF5es s\xE3o sempre tratados por uma pessoa."
  ].join("\n"),
  /**
   * Where the price came from: the site's own wording (RECEIPT_CREDIT, /metodologia, /bot). Calm, no tax talk, and
   * what we do not touch: the bar's systems and the buyer.
   */
  fonte: [
    "De fontes p\xFAblicas, como notas fiscais (Pre\xE7o da Hora Bahia, SEFAZ-BA), card\xE1pios que os bares mandam e envios de quem frequenta.",
    "A gente n\xE3o acessa o sistema do bar e nunca v\xEA dados de quem comprou.",
    "Se um pre\xE7o estiver errado, a gente corrige ou tira o bar do site em at\xE9 24 horas."
  ].join("\n"),
  /**
   * What is free. The site and this Raio-X are; a paid destaque exists and a person explains it. No price, no date, no
   * term. "De graça" once. Expires with the free period (31/01/2027).
   */
  gratis: [
    "Estar no site e receber o Raio-X por aqui \xE9 de gra\xE7a. Nada \xE9 cobrado sem um sim de voc\xEAs.",
    "Existe tamb\xE9m um destaque opcional, que \xE9 pago. Se quiserem saber como funciona, escrevam PESSOA e uma pessoa explica."
  ].join("\n"),
  /** How to fix a price: the 24-hour promise of DM 1 (LEAD_CORRECTION_LINE). */
  corrigir: [
    "Mandem aqui o pre\xE7o certo, com o tamanho (lata, long neck, 600 ml, chopp), ou uma foto do card\xE1pio.",
    "A gente corrige, ou tira o bar do site, em at\xE9 24 horas."
  ].join("\n"),
  /** Take my bar off the site. Line 3 keeps "tirar do site" and "parar as mensagens" apart: bars mix them up. */
  remover: [
    "Para tirar o bar do site, \xE9 s\xF3 pedir por aqui: a gente tira em at\xE9 24 horas.",
    "Se for s\xF3 um pre\xE7o errado, mandem o pre\xE7o certo que a gente corrige.",
    "Para n\xE3o receber mais mensagens, escrevam SAIR."
  ].join("\n"),
  /**
   * What is kept and for how long. Matches KEEP_SECONDS (90 days) and /privacidade ("o contato é apagado 90 dias após a
   * última interação"; the history stays without the contact). "Lidas pelo assistente e por uma pessoa" is the truth.
   */
  dados: [
    "O n\xFAmero e as mensagens desta conversa ficam guardados por 90 dias depois da \xFAltima mensagem; depois, s\xE3o apagados.",
    "As mensagens s\xE3o lidas pelo assistente autom\xE1tico e por uma pessoa, s\xF3 para atender voc\xEAs. Detalhes: {site}/privacidade",
    "Para parar de receber mensagens, escrevam SAIR; para apagar antes, escrevam PESSOA."
  ].join("\n"),
  /** If I pay, do I go up? No. The paid destaque is said to exist because the bar has just asked about paying. */
  ranking: [
    "N\xE3o: a ordem \xE9 sempre pelo pre\xE7o por litro, e ningu\xE9m paga para subir.",
    'Mesmo o destaque pago fica \xE0 parte, num cart\xE3o marcado "Patrocinado" acima da lista, e a lista n\xE3o muda.'
  ].join("\n"),
  /**
   * The badge: ours, free, six months (VERIFIED_DAYS = 180), renewed by a new menu, never moves the ranking. "Não é do
   * Instagram": DM 1 says "como no Instagram" (the owner's choice of 12 and 13 Oct), which is about how it looks. The
   * owner to confirm that the two read as one story.
   */
  selo: [
    'O selo azul "Bar verificado" \xE9 do nosso site, n\xE3o do Instagram: mostra que o pr\xF3prio bar mandou os pre\xE7os.',
    "\xC9 de gra\xE7a, vale por seis meses (e renova com um card\xE1pio novo) e n\xE3o muda a posi\xE7\xE3o no ranking."
  ].join("\n"),
  /** How much work: none required; a photo when prices change. No minutes promised (the bar-owner's "five minutes" is a guess). */
  tempo: [
    "Nenhum trabalho \xE9 obrigat\xF3rio.",
    "Para manter os pre\xE7os em dia, basta mandar uma foto do card\xE1pio aqui mesmo quando eles mudarem."
  ].join("\n"),
  /** Will it bring customers? We can't promise. No number, no "mais clientes", no date, no term. Expires with the first results. */
  resultado: [
    "Isso a gente n\xE3o pode prometer: o site \xE9 novo e ainda n\xE3o tem resultados de bares para mostrar.",
    "O que ele faz \xE9 mostrar o pre\xE7o da cerveja de voc\xEAs, com a data, para quem compara antes de sair.",
    "A gente s\xF3 fala de resultado quando tiver n\xFAmero."
  ].join("\n"),
  /**
   * "Isso é golpe?" Calm, no argument: what nobody here asks for (a password, a verification code), that the assistant
   * never asks for payment and that a charge only starts with the bar's yes, arranged by a person, then where to
   * check and how to stop. It never says "ninguém pede Pix" or "ninguém pede pagamento": the recap names R$ 250 and
   * Pix, and in January a person asks. No selo lines: when the bar mentions the selo ("o golpe do selo azul"), the
   * code sends FAQ selo as a second message. The last line depends on the number being on /para-bares (see EXPIRES).
   */
  golpe: [
    "Faz sentido desconfiar: tem muito golpe por a\xED. Aqui ningu\xE9m pede senha nem c\xF3digo de verifica\xE7\xE3o.",
    "O assistente autom\xE1tico nunca pede pagamento. Qualquer cobran\xE7a s\xF3 acontece com um sim de voc\xEAs, combinada com uma pessoa.",
    "Podem conferir em {site}/para-bares: este mesmo n\xFAmero est\xE1 l\xE1. Para parar tudo, escrevam SAIR."
  ].join("\n"),
  /** Can I send the menu photo here? Yes. */
  cardapio: [
    "Podem mandar a foto do card\xE1pio aqui mesmo, no chat: j\xE1 serve.",
    "N\xE3o precisa de link, de senha nem de pagamento."
  ].join("\n")
};
var WA_PROFILE = {
  /** The line under the name. "Escreva": the profile is public, so it speaks to "você". */
  about: "Pre\xE7o da cerveja nos bares, do mais barato ao mais caro. Respostas autom\xE1ticas; para falar com uma pessoa, escreva PESSOA.",
  /**
   * No city (national brand), no company claim, no source name that would alarm a bar before it says hello, and bars only:
   * the site also lists shops, apart, but a bar owner reading "depósitos" here thinks of its competitors.
   */
  description: [
    "O Cerveja Barata compara o pre\xE7o da cerveja nos bares, do mais barato ao mais caro por litro, com a data de cada pre\xE7o.",
    `Dono de bar? Mande RAIO-X e receba o pre\xE7o do litro do seu bar ao lado da m\xE9dia do bairro, de gra\xE7a. As respostas aqui s\xE3o autom\xE1ticas: escreva PESSOA para falar com uma pessoa, que responde ${WA_REPLY_WITHIN}. Escreva SAIR para parar de receber mensagens.`,
    `cervejabarata.com.br
${LEAD_CONAR_LINE}`
  ].join("\n\n")
};
var IG_CAPTION_CTA = "Dono de bar? Comente RAIO-X ou chame no WhatsApp (link na bio) e veja o litro do seu bar ao lado da m\xE9dia do bairro.";
var IG_CAPTION_CTA_LIVE = false;
var IG_COMMENT_PRIVATE_REPLY = [
  "Oi! Aqui \xE9 o Cerveja Barata ({site}), respondendo ao coment\xE1rio de voc\xEAs.",
  "O Raio-X de voc\xEAs (o litro do bar ao lado da m\xE9dia do bairro) sai pelo WhatsApp. \xC9 s\xF3 enviar a mensagem pronta: {wa_link}",
  "A resposta l\xE1 \xE9 autom\xE1tica e de gra\xE7a. Se n\xE3o quiserem receber mensagens, \xE9 s\xF3 avisar."
].join("\n");
var IG_DM_REPLY = [
  "Oi! Aqui \xE9 o Cerveja Barata ({site}). Esta \xE9 uma resposta autom\xE1tica.",
  "O atendimento \xE9 pelo WhatsApp. Enviem a mensagem pronta neste link para receber o Raio-X ou tirar d\xFAvidas: {wa_link}",
  "Para n\xE3o receber mais mensagens, escrevam SAIR por aqui."
].join("\n");
var IG_STORY_MENTION_REPLY = [
  "Obrigado por marcar a gente nos stories!",
  "Dono de bar? O Raio-X (o litro do bar ao lado da m\xE9dia do bairro) sai pelo WhatsApp: {wa_link}",
  "Se n\xE3o quiserem receber mensagens, \xE9 s\xF3 avisar."
].join("\n");
var IG_OPTOUT_CONFIRM = [
  "Pronto: a gente n\xE3o escreve mais para esta conta.",
  "Se tamb\xE9m quiserem tirar o bar do site, \xE9 s\xF3 pedir por aqui: a gente tira em at\xE9 24 horas."
].join("\n");

// apps/site/src/brand.ts
var BRAND = {
  name: "Cerveja Barata",
  domain: "cervejabarata.com.br",
  /** Brand addresses only (Cloudflare Email Routing to a private inbox); never a personal one. */
  email: {
    contato: "contato@cervejabarata.com.br",
    privacidade: "privacidade@cervejabarata.com.br",
    imprensa: "imprensa@cervejabarata.com.br",
    bares: "bares@cervejabarata.com.br"
  },
  /**
   * The brand's own public profiles, for Organization.sameAs: only ones that exist, each run by the brand (the GitHub
   * organisation holds the public scrapers; its members stay private).
   */
  profiles: [
    "https://www.instagram.com/cervejabaratacombr/",
    "https://github.com/cb-data-org"
  ],
  /**
   * The logo: "cb," on navy with the coral comma (#846), a 512 px PNG (public/logo.png, drawn by
   * scripts/brand-icons.py) for Organization.logo and the brand's profile pictures. Keep the URL: search engines and
   * profiles keep a copy.
   */
  logo: { path: "/logo.png", width: 512, height: 512 },
  /**
   * The brand's WhatsApp number (digits, with 55), where bars ask for their Raio-X and the sales bot answers
   * (docs/sales-automation.md §4). Empty: the WhatsApp buttons stay hidden, until the owner publishes the number.
   */
  whatsapp: "5571999668268",
  /** The year the brand started (Organization.foundingDate). */
  founded: "2026",
  tagline: { "pt-BR": "o mapa da cerveja barata", en: "the cheap beer map" },
  /** What the site does, after the name's comma in the header (#884; the copywriter's words of 28 Oct 2026). */
  descriptor: { "pt-BR": "o mapa dos pre\xE7os por litro", en: "a map of beer prices per litre" },
  headline: { "pt-BR": "A cerveja mais barata perto de voc\xEA", en: "The cheapest beer near you" }
};

// apps/site/src/seo/titles.ts
var SUFFIX = ` \xB7 ${BRAND.name}`;

// apps/site/src/city/dates.ts
var count = (n, locale) => new Intl.NumberFormat(locale === "en" ? "en-GB" : "pt-BR").format(n);

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
var plural = (n, one, many) => `${count(n, "pt-BR")} ${n === 1 ? one : many}`;
var pluralEn = (n, one, many) => `${count(n, "en")} ${n === 1 ? one : many}`;
function among(s, locale) {
  const recentPt = s.recent ? "" : " conferido";
  const recentEn = s.recent ? "a price" : "a checked price";
  if (locale === "en") {
    if (s.places) return `among ${pluralEn(s.venues, ...s.places)} in ${s.city} with ${recentEn}`;
    return s.venues === 1 ? `at the only ${s.city} bar with ${recentEn}` : `among the ${count(s.venues, "en")} ${s.city} bars with ${recentEn}`;
  }
  if (s.places) return `entre ${plural(s.venues, ...s.places)} de ${s.city} com pre\xE7o${recentPt}`;
  return s.venues === 1 ? `no \xFAnico bar de ${s.city} com pre\xE7o${recentPt}` : `entre os ${count(s.venues, "pt-BR")} bares de ${s.city} com pre\xE7o${recentPt}`;
}
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
  // "no Dois de Julho", as Correio and A Tarde write it.
  "dois de julho": "no",
  "santo antonio alem do carmo": "no",
  "pelourinho e centro historico": "no",
  barris: "nos",
  mares: "nos"
};
var placeKey = (name) => name.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
function inPlace(name) {
  return `${ARTICLES[placeKey(name)] ?? "em"} ${name}`;
}
var capitalFirst = (text) => `${text.charAt(0).toUpperCase()}${text.slice(1)}`;
var DISPLAY_NAMES = {
  "salvador:santo-antonio": "Santo Ant\xF4nio Al\xE9m do Carmo",
  "salvador:centro-historico": "Pelourinho e Centro Hist\xF3rico"
};
var bairroDisplayName = (city, slug, name) => DISPLAY_NAMES[`${city}:${slug}`] ?? name;
var COPY = {
  "pt-BR": {
    /** Title candidates, longest first: the page takes the first that fits 60 characters. */
    title: (city) => [
      `Cerveja barata em ${city}: bares mais baratos de hoje`,
      `Cerveja barata em ${city}: bares mais baratos`
    ],
    /** Happy hours only when the city has some: the description is what search engines and assistants quote. */
    description: (city, happyHours) => `Os pre\xE7os de cerveja mais baixos de ${city}, do mais barato ao mais caro por litro, com ${happyHours ? "happy hours, " : ""}bairros e mapa.`,
    heading: (city) => `A cerveja mais barata em ${city}`,
    categories: { bar: "Bares", mercado: "Mercados", deposito: "Dep\xF3sitos" },
    categoryHeading: {
      mercado: (city) => `A cerveja mais barata nos mercados de ${city}`,
      deposito: (city) => `A cerveja mais barata nos dep\xF3sitos de ${city}`
    },
    categoryTitle: {
      mercado: (city) => [
        `Cerveja barata no mercado em ${city}: pre\xE7os de hoje`,
        `Cerveja barata no mercado em ${city}`
      ],
      deposito: (city) => [
        `Dep\xF3sito de bebidas em ${city}: cerveja mais barata hoje`,
        `Dep\xF3sito de bebidas em ${city}: cerveja mais barata`
      ]
    },
    categoryDescription: {
      mercado: (city) => `Pre\xE7o da cerveja nos supermercados e mercadinhos de ${city}, das notas fiscais das \xFAltimas horas, em ordem de pre\xE7o por litro.`,
      deposito: (city) => `Pre\xE7o da cerveja nos dep\xF3sitos e distribuidoras de ${city}, das notas fiscais das \xFAltimas horas, em ordem de pre\xE7o por litro.`
    },
    categoryPlaces: { mercado: ["loja", "lojas"], deposito: ["dep\xF3sito", "dep\xF3sitos"] },
    categoryWhere: { mercado: "nos mercados", deposito: "nos dep\xF3sitos" },
    categoryNote: "Pre\xE7os de notas fiscais (NFC-e) das \xFAltimas 72 horas, do Pre\xE7o da Hora Bahia (SEFAZ-BA). Pre\xE7o de loja muda com cada promo\xE7\xE3o: sai do ranking depois de 14 dias. Venda proibida para menores de 18 anos.",
    /**
     * The claim is scoped to the bars we have prices for (audit A12): the press knows cheap bars we don't price yet.
     * Then the typical prices (a sentence of answers.ts), or how many prices the claim rests on.
     */
    summary: (s, typical) => `Em ${s.date}, o menor pre\xE7o por litro${s.recent ? " conferido nos \xFAltimos 90 dias" : ""} ${among(s, "pt-BR")} \xE9 ${s.perLitre}: ${s.beer}, ${s.serving}, por ${s.price}${kindNote(s.kind, "pt-BR")} \u2014 ${s.venue}. ` + (typical ?? `Comparamos ${plural(s.prices, "pre\xE7o", "pre\xE7os")} em ${plural(s.venues, ...s.places ?? ["bar", "bares"])}.`),
    summaryEmpty: (date, city) => `Em ${date}, ainda n\xE3o temos pre\xE7os publicados em ${city}. Os primeiros chegam em breve.`,
    /** "R$ 9,00 a lata de 350 ml": a typical price inside a list. */
    typicalItem: (price, serving) => `${price} ${serving}`,
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
    results: (n) => n === 1 ? "1 pre\xE7o" : `${count(n, "pt-BR")} pre\xE7os`,
    bars: (n) => n === 1 ? "1 bar" : `${count(n, "pt-BR")} bares`,
    morePrices: (n) => n === 1 ? "+1 pre\xE7o" : `+${n} pre\xE7os`,
    noResults: "Nenhum pre\xE7o com esses filtros.",
    showMore: "Ver mais",
    loadingList: "Carregando todos os pre\xE7os\u2026",
    listFailed: "N\xE3o deu para carregar todos os pre\xE7os. Tente de novo.",
    locating: "Procurando voc\xEA\u2026",
    locationDenied: "N\xE3o deu para saber onde voc\xEA est\xE1. Escolha um bairro.",
    away: (km) => km < 1 ? `a ${Math.round(km * 1e3)} m` : `a ${km.toFixed(1).replace(".", ",")} km`,
    service: (pct) => `+${pct} % servi\xE7o`,
    couvert: (price) => `couvert ${price}`,
    confirmed: (n) => `confirmado por ${n} pessoas`,
    creditedTo: (nickname, level) => `enviado por @${nickname}${level ? ` \xB7 ${level}` : ""}`,
    disputed: "contestado",
    openBar: "Open bar",
    openBarIntro: "Pre\xE7o fixo por um tempo definido: \xE9 o pre\xE7o do pacote, sem pre\xE7o por litro.",
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
    /** The headline card's heading: the cheapest litre where the page looks (it never repeats the page's H1). */
    cheapestLitre: {
      bar: "O litro mais barato nos bares",
      mercado: "O litro mais barato nos mercados",
      deposito: "O litro mais barato nos dep\xF3sitos"
    },
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
    // English titles always say "<city>, Brazil": English search mixes Salvador up with El Salvador.
    title: (city) => [
      `Cheap beer in ${city}, Brazil: cheapest bars today`,
      `Cheap beer in ${city}, Brazil: cheapest bars`
    ],
    description: (city, happyHours) => `The lowest beer prices in ${city}, ranked by price per litre, with ${happyHours ? "happy hours, " : ""}neighbourhoods and a map.`,
    heading: (city) => `The cheapest beer in ${city}, Brazil`,
    categories: { bar: "Bars", mercado: "Supermarkets", deposito: "Dep\xF3sitos" },
    categoryHeading: {
      mercado: (city) => `The cheapest beer in ${city}'s supermarkets`,
      deposito: (city) => `The cheapest beer in ${city}'s dep\xF3sitos`
    },
    categoryTitle: {
      mercado: (city) => [
        `Supermarket beer prices in ${city}, Brazil today`,
        `Supermarket beer prices in ${city}, Brazil`
      ],
      deposito: (city) => [
        `Drink shops (dep\xF3sitos) in ${city}, Brazil: cheapest beer`,
        `Drink shops in ${city}, Brazil: cheapest beer`
      ]
    },
    categoryDescription: {
      mercado: (city) => `Beer prices in ${city}'s supermarkets and corner shops, from the last hours' receipts, ranked by price per litre.`,
      deposito: (city) => `Beer prices in ${city}'s dep\xF3sitos (drink shops), from the last hours' receipts, ranked by price per litre.`
    },
    categoryPlaces: { mercado: ["shop", "shops"], deposito: ["dep\xF3sito", "dep\xF3sitos"] },
    categoryWhere: { mercado: "supermarkets", deposito: "dep\xF3sitos" },
    categoryNote: "Prices from electronic receipts (NFC-e) of the last 72 hours, via Bahia's Pre\xE7o da Hora (SEFAZ-BA). Shop prices change with every promotion: they leave the ranking after 14 days. No sales to under-18s.",
    summary: (s, typical) => `On ${s.date}, the lowest price per litre${s.recent ? " checked in the last 90 days" : ""} ${among(s, "en")} is ${s.perLitre}: ${s.beer}, ${s.serving}, for ${s.price}${kindNote(s.kind, "en")} \u2014 ${s.venue}. ` + (typical ?? `We compared ${pluralEn(s.prices, "price", "prices")} at ${pluralEn(s.venues, ...s.places ?? ["bar", "bars"])}.`),
    summaryEmpty: (date, city) => `As of ${date}, there are no published prices in ${city} yet. The first ones are coming soon.`,
    typicalItem: (price, serving) => `${price} for ${serving}`,
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
    results: (n) => n === 1 ? "1 price" : `${count(n, "en")} prices`,
    bars: (n) => n === 1 ? "1 bar" : `${count(n, "en")} bars`,
    morePrices: (n) => n === 1 ? "+1 price" : `+${n} prices`,
    noResults: "No prices match these filters.",
    showMore: "Show more",
    loadingList: "Loading every price\u2026",
    listFailed: "Couldn't load every price. Try again.",
    locating: "Finding you\u2026",
    locationDenied: "We couldn't tell where you are. Pick a neighbourhood instead.",
    away: (km) => km < 1 ? `${Math.round(km * 1e3)} m away` : `${km.toFixed(1)} km away`,
    service: (pct) => `+${pct}% service`,
    couvert: (price) => `cover ${price}`,
    confirmed: (n) => `confirmed by ${n} people`,
    creditedTo: (nickname, level) => `sent by @${nickname}${level ? ` \xB7 ${level}` : ""}`,
    disputed: "disputed",
    openBar: "Open bar",
    openBarIntro: "A fixed price for a set time: it is a package price, with no price per litre.",
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
    cheapestLitre: {
      bar: "The cheapest litre in bars",
      mercado: "The cheapest litre in supermarkets",
      deposito: "The cheapest litre in dep\xF3sitos"
    },
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
  priceGuide: { "pt-BR": "/:city/preco-da-cerveja", en: "/en/:city/beer-prices" },
  neighbourhood: { "pt-BR": "/:city/:bairro", en: "/en/:city/:bairro" },
  happyHour: { "pt-BR": "/:city/happy-hour", en: "/en/:city/happy-hour" },
  happyHourDay: { "pt-BR": "/:city/happy-hour/:day", en: "/en/:city/happy-hour/:day" },
  openBar: { "pt-BR": "/:city/open-bar", en: "/en/:city/open-bar" },
  venue: { "pt-BR": "/:city/bares/:venue", en: "/en/:city/bars/:venue" },
  // Shops have pages of their own under their lists (audit T02); their old /bares/ URLs move for good (src/worker.ts).
  supermarket: { "pt-BR": "/:city/mercados/:venue", en: "/en/:city/supermarkets/:venue" },
  depot: { "pt-BR": "/:city/depositos/:venue", en: "/en/:city/depots/:venue" },
  brand: { "pt-BR": "/:city/cerveja/:brand", en: "/en/:city/beer/:brand" },
  format: { "pt-BR": "/:city/formato/:format", en: "/en/:city/format/:format" },
  // Not "/en/:city/index": a last segment named index reads as a directory index (Cloudflare redirects it to "/").
  priceIndex: { "pt-BR": "/:city/indice", en: "/en/:city/price-index" },
  priceIndexMonth: { "pt-BR": "/:city/indice/:month", en: "/en/:city/price-index/:month" },
  // The next edition as this build's prices would make it, for checking before the 1st: internal, never indexed.
  priceIndexPreview: { "pt-BR": "/:city/indice/previa" },
  newYearsEve: { "pt-BR": "/:city/reveillon", en: "/en/:city/new-years-eve" },
  carnival: { "pt-BR": "/:city/carnaval", en: "/en/:city/carnival" },
  // The Friday Top 10, frozen with its date, and the archive of each week's cheapest (src/city/sextou.ts).
  sextou: { "pt-BR": "/:city/sextou", en: "/en/:city/friday-top-10" },
  // The guias, editorial articles about a city (src/guias): Portuguese only, so no English route and no hreflang.
  guias: { "pt-BR": "/:city/guias" },
  guia: { "pt-BR": "/:city/guias/:guia" },
  // "Atualizar um preço" (src/components/PriceUpdatePage.astro): the bar's or shop's staff say a price changed, with
  // proof. Portuguese only and internal (never indexed, no twin): people arrive from a venue page's owner line, which
  // names the place in ?venue=<slug> (priceUpdatePath).
  atualizarPreco: { "pt-BR": "/:city/atualizar-preco" },
  // National, no city: the price-per-litre calculator (src/city/price-per-litre.ts).
  pricePerLitre: { "pt-BR": "/preco-por-litro", en: "/en/price-per-litre" },
  data: { "pt-BR": "/dados", en: "/en/data" },
  developers: { "pt-BR": "/desenvolvedores", en: "/en/developers" },
  press: { "pt-BR": "/imprensa", en: "/en/press" },
  changelog: { "pt-BR": "/atualizacoes", en: "/en/changelog" },
  forBars: { "pt-BR": "/para-bares", en: "/en/for-bars" },
  forBarsFounders: { "pt-BR": "/para-bares/fundadores", en: "/en/for-bars/founders" },
  forBarsMenu: { "pt-BR": "/para-bares/cardapio", en: "/en/for-bars/menu" },
  forBarsBadge: { "pt-BR": "/para-bares/selo", en: "/en/for-bars/badge" },
  submit: { "pt-BR": "/enviar", en: "/en/submit" },
  submitBar: { "pt-BR": "/enviar/bar", en: "/en/submit/bar" },
  about: { "pt-BR": "/sobre", en: "/en/about" },
  methodology: { "pt-BR": "/metodologia", en: "/en/methodology" },
  privacy: { "pt-BR": "/privacidade", en: "/en/privacy" },
  terms: { "pt-BR": "/termos", en: "/en/terms" },
  bot: { "pt-BR": "/bot", en: "/en/bot" },
  styleGuide: { "pt-BR": "/estilo" },
  // Tables for other sites to embed (the press kit, src/press/kit.ts): "/embed" and the address of the page they
  // come from, never indexed (their canonical URL is that page), and the only pages any site may frame
  // (embedRules in scripts/header-rules.mjs).
  embedSizes: { "pt-BR": "/embed/:city/preco-da-cerveja/formatos", en: "/en/embed/:city/beer-prices/sizes" },
  embedPremiums: {
    "pt-BR": "/embed/:city/preco-da-cerveja/bar-ou-mercado",
    en: "/en/embed/:city/beer-prices/bar-or-supermarket"
  },
  embedBairros: {
    "pt-BR": "/embed/:city/preco-da-cerveja/bairros",
    en: "/en/embed/:city/beer-prices/neighbourhoods"
  },
  embedNeighbourhood: { "pt-BR": "/embed/:city/:bairro", en: "/en/embed/:city/:bairro" },
  embedIndexMonth: { "pt-BR": "/embed/:city/indice/:month", en: "/en/embed/:city/price-index/:month" }
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
  const top = /* @__PURE__ */ new Set(["en", "api", "hooks", "mcp", "healthz", "_astro", "selos"]);
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
  const slug = value("venue_slug");
  return {
    id: row.id,
    venueId: row.venue_id,
    city: row.city,
    venueSlug: slug,
    venueName: venueDisplayName(value("venue_name") ?? "", { city: row.city, slug }),
    venueCategory: venueCategory(venue?.kind, venue?.on_site ?? 1),
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
function listable(deal, now, timeZone) {
  if (deal.verifiedAt === null || disputeState(deal.disputes) === "hidden") return false;
  if (deal.category !== "bar") return ageInDays(deal.verifiedAt, now, timeZone) <= SHOP_PRICE_DAYS;
  return inRankings(freshnessOf(deal.verifiedAt, now, timeZone));
}
function rankDeals(deals, now, timeZone, category = "bar", alcoholFree = false) {
  return deals.filter(
    (deal) => deal.category === category && deal.nonAlcoholic === alcoholFree && deal.kind !== "open_bar" && !scheduleMissing(deal.kind, deal.windows) && listable(deal, now, timeZone)
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
function servingLabel(deal, locale, inSentence2 = false) {
  const lower = (text) => inSentence2 ? `${text.charAt(0).toLowerCase()}${text.slice(1)}` : text;
  const format2 = formatByCode(deal.formatCode);
  const name = lower(
    format2 ? format2.label[locale] : `${FORMAT_TYPE_LABELS[locale][deal.formatType] ?? deal.formatType} ${deal.volumeMl} ml`
  );
  if (deal.packaging === "single" && deal.units === 1) return name;
  const packaging = PACKAGING_LABELS[locale][deal.packaging];
  const amount = deal.units > 1 ? `${deal.units} \xD7 ${name}` : name;
  return packaging ? `${lower(packaging)}: ${amount}` : amount;
}
function beerLabel(deal, locale) {
  return deal.beerName ?? deal.brandName ?? COPY[locale].anyBeer;
}
var money = (minor) => formatMoney(minor, market);
var perLitre2 = (minor) => formatPpn(minor, market);

// apps/site/src/city/venues.ts
var generatedAtOf = (data) => Math.floor(Date.parse(data.manifest.generated_at) / 1e3);

// apps/site/src/city/landing.ts
var rankingsCache = /* @__PURE__ */ new WeakMap();
function cityRankings(data) {
  const cached = rankingsCache.get(data);
  if (cached) return cached;
  const generatedAt = generatedAtOf(data);
  const venues = new Map(data.venues.map((row) => [row.id, row]));
  const rankings = data.cities.map((city) => {
    const deals = data.deals.filter((row) => row.city === city.slug).map((row) => toCityDeal(row, venues));
    return {
      city,
      generatedAt,
      ranked: rankDeals(deals, generatedAt, city.timezone),
      alcoholFree: rankDeals(deals, generatedAt, city.timezone, "bar", true)
    };
  });
  rankingsCache.set(data, rankings);
  return rankings;
}
function bairroPages(rankings) {
  return rankings.flatMap((ranking2) => {
    const byBairro = /* @__PURE__ */ new Map();
    for (const deal of ranking2.ranked) {
      if (deal.bairro) byBairro.set(deal.bairro, [...byBairro.get(deal.bairro) ?? [], deal]);
    }
    return [...byBairro].filter(([, deals]) => new Set(deals.map((deal) => deal.venueId)).size >= BAIRRO_MIN_VENUES).map(([slug, deals]) => {
      const name = deals[0]?.bairroName ?? slug;
      return {
        ...ranking2,
        ranked: deals,
        alcoholFree: (ranking2.alcoholFree ?? []).filter((deal) => deal.bairro === slug),
        bairro: { slug, name, display: bairroDisplayName(ranking2.city.slug, slug, name) },
        cityRanked: ranking2.ranked
      };
    });
  });
}
var venuesIn = (deals) => new Set(deals.map((deal) => deal.venueId)).size;

// apps/site/src/city/sextou.ts
var SEXTOU_TOP = 10;
var SEXTOU_MIN_BARS = 10;
var SEXTOU_FRESH_DAYS = 14;
var SEXTOU_NAMED_DAYS = 30;
var FRIDAY = 5;
var DAY3 = 86400;
var dayOfWeek = (iso) => (/* @__PURE__ */ new Date(`${iso}T12:00:00Z`)).getUTCDay();
var isFriday = (day) => dayOfWeek(day) === FRIDAY;
function freshBars(ranked, now) {
  return new Set(
    ranked.filter((deal) => deal.verifiedAt !== null && now - deal.verifiedAt <= SEXTOU_FRESH_DAYS * DAY3).map((deal) => deal.venueId)
  ).size;
}
function rowOf(deal, rank) {
  return {
    rank,
    id: deal.id,
    venueId: deal.venueId,
    venueSlug: deal.venueSlug,
    venueName: deal.venueName,
    venueCategory: deal.venueCategory,
    bairro: deal.bairro,
    bairroName: deal.bairroName,
    beerName: deal.beerName,
    brand: deal.brand,
    brandName: deal.brandName,
    formatCode: deal.formatCode,
    formatType: deal.formatType,
    volumeMl: deal.volumeMl,
    units: deal.units,
    packaging: deal.packaging,
    priceMinor: deal.priceMinor,
    ppnMinor: deal.ppnMinor,
    kind: deal.kind,
    windows: deal.windows,
    verifiedAt: deal.verifiedAt
  };
}
function dealOf(row, city) {
  return {
    id: row.id,
    credit: null,
    venueId: row.venueId,
    city,
    venueSlug: row.venueSlug,
    venueName: row.venueName,
    venueCategory: row.venueCategory,
    bairro: row.bairro,
    bairroName: row.bairroName,
    lat: 0,
    lng: 0,
    beerName: row.beerName,
    nonAlcoholic: false,
    brand: row.brand,
    brandName: row.brandName,
    formatCode: row.formatCode,
    formatType: row.formatType,
    volumeMl: row.volumeMl,
    units: row.units,
    packaging: row.packaging,
    priceMinor: row.priceMinor,
    ppnMinor: row.ppnMinor,
    kind: row.kind,
    openBarMinutes: null,
    conditions: null,
    servicePct: null,
    couvertMinor: null,
    windows: row.windows,
    verifiedAt: row.verifiedAt,
    confirmations: 0,
    disputes: 0,
    category: "bar",
    venueVerifiedAt: null
  };
}
function freezeWeek(ranking2, day) {
  if (!isFriday(day)) return null;
  const bars = freshBars(ranking2.ranked, ranking2.generatedAt);
  if (bars < SEXTOU_MIN_BARS) return null;
  return {
    friday: day,
    frozenAt: ranking2.generatedAt,
    bars,
    prices: ranking2.ranked.length,
    top: onePerBar(ranking2.ranked).slice(0, SEXTOU_TOP).map((deal, index) => rowOf(deal, index + 1))
  };
}
function addWeek(weeks, week) {
  const known = week && weeks.some((each) => each.friday === week.friday);
  return [...weeks, ...week && !known ? [week] : []].sort((a, b) => b.friday.localeCompare(a.friday));
}
var liveBars = (venues) => new Set(
  venues.filter((venue) => venueCategory(venue.kind, venue.on_site) === "bar").map((venue) => venue.id)
);
var namesBar = (row, frozenAt, live) => live.has(row.venueId) && row.verifiedAt !== null && frozenAt - row.verifiedAt <= SEXTOU_NAMED_DAYS * DAY3;
function anonymousBar(bairroName, cityName, locale) {
  const place = bairroName ?? cityName;
  return locale === "en" ? `a bar in ${place}` : `um bar ${inPlace(place)}`;
}
function shownDeal(row, week, city, live, locale) {
  const deal = dealOf(row, city.slug);
  if (namesBar(row, week.frozenAt, live)) return { ...deal, named: true, cityOnly: false };
  return {
    ...deal,
    venueId: 0,
    venueSlug: "",
    venueName: anonymousBar(row.bairroName, city.name, locale),
    bairro: null,
    bairroName: null,
    named: false,
    cityOnly: row.bairroName === null
  };
}
var rowName = (deal) => deal.named ? deal.venueName : capitalFirst(deal.venueName);

// tools/scripts/src/social/archive.ts
function readWeeks(file, city) {
  if (!existsSync2(file)) return [];
  const body = JSON.parse(readFileSync2(file, "utf8"));
  if (body.city !== city || !Array.isArray(body.items))
    throw new Error(`${file}: not ${city}'s Friday archive`);
  return body.items;
}
var archiveText = (city, weeks, now) => `${JSON.stringify(
  {
    city,
    updated_at: now.toISOString(),
    license: LICENSE,
    attribution: ATTRIBUTION,
    items: [...weeks]
  },
  null,
  1
)}
`;
function freezeArchive(file, data, city, now = /* @__PURE__ */ new Date()) {
  const ranking2 = cityRankings(data).find((row) => row.city.slug === city);
  if (!ranking2) throw new Error(`unknown city: ${city}`);
  const today = localDate(Math.floor(now.getTime() / 1e3), ranking2.city.timezone);
  const weeks = readWeeks(file, city);
  const updated = addWeek(weeks, freezeWeek(ranking2, today));
  const added = updated.length > weeks.length;
  if (added) {
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, archiveText(city, updated, now));
  }
  return { added, frozen: updated.find((week) => week.friday === today) ?? null };
}

// tools/scripts/src/social/jpeg.ts
import sharp from "sharp";
var JPEG_QUALITY = 92;
async function toJpeg(png) {
  return sharp(png).removeAlpha().toColourspace("srgb").withIccProfile("srgb").jpeg({ quality: JPEG_QUALITY, chromaSubsampling: "4:4:4" }).toBuffer();
}

// tools/scripts/src/social/posts.ts
var POST_MIN_BARS = 5;
var TOP = 5;
var MAX_BAIRRO_POSTS = 4;
var SITE = "cervejabarata.com.br";
var INSTAGRAM = "@cervejabaratacombr";
var CONAR = "Beba com modera\xE7\xE3o \xB7 Se beber, n\xE3o dirija \xB7 Proibido para menores de 18 anos";
var TAG_FRESH_DAYS = 30;
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
  const parts2 = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date(at * 1e3)).map((part) => [part.type, part.value])
  );
  const local = new Date(Date.UTC(Number(parts2.year), Number(parts2.month) - 1, Number(parts2.day)));
  local.setUTCDate(local.getUTCDate() - (local.getUTCDay() + 6) % 7);
  return new Intl.DateTimeFormat("pt-BR", { day: "numeric", month: "long", timeZone: "UTC" }).format(local);
}
var plain2 = (text) => text.replace(/ /g, " ");
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
  const frozen = options.frozen ?? null;
  const live = liveBars(data.venues);
  const post = (ranked, bairro) => {
    const place = bairro?.name ?? city.name;
    const route = bairro ? path("neighbourhood", "pt-BR", { city: city.slug, bairro: bairro.slug }) : path("city", "pt-BR", { city: city.slug });
    const top = !bairro && frozen ? [...frozen.top].sort((a, b) => a.rank - b.rank).slice(0, TOP).map((row) => {
      const deal = shownDeal(row, frozen, city, live, "pt-BR");
      return entryOf({ ...deal, venueName: rowName(deal), more: 0 }, row.rank);
    }) : onePerBar(ranked).slice(0, TOP).map((deal, index) => entryOf(deal, index + 1));
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
      top,
      sextou: !bairro && frozen ? `${SITE}${path("sextou", "pt-BR", { city: city.slug })}` : null
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
var MAX_HASHTAGS = 5;
function caption(post) {
  const first = post.top[0];
  if (!first) throw new Error(`${post.key}: no prices`);
  const tags = [
    hashtag(post.city.name),
    ...post.bairro ? [hashtag(post.bairro.name)] : [],
    "#cervejabarata",
    "#cerveja",
    "#happyhour",
    "#boteco",
    "#cervejagelada",
    "#bahia"
  ].slice(0, MAX_HASHTAGS);
  return plain2(
    [
      `${post.headline} nesta semana sai a ${first.perLitre.replace("/L", " o litro")}. Veja onde.`,
      [
        `Top 3 por pre\xE7o por litro, semana de ${post.week}:`,
        ...post.top.slice(0, 3).map((entry) => captionLine(entry, !post.bairro))
      ].join("\n"),
      "Pre\xE7o por litro para comparar lata, long neck, garrafa e chopp: quanto menor, mais cerveja pelo seu dinheiro. S\xF3 bares, um pre\xE7o por bar (o mais barato).",
      `O top ${post.top.length} est\xE1 no carrossel. O mapa completo, com ${post.bars} bares ${inPlace(post.bairro?.name ?? post.city.name)}, est\xE1 no link da bio: ${post.url}`,
      ...post.sextou ? [`O top 10 desta sexta e a mais barata de cada semana: ${post.sextou}`] : [],
      `Dados de ${post.updated}; os pre\xE7os podem mudar. Viu um pre\xE7o diferente? Avise pelo site.`,
      // Once the brand's WhatsApp is published, the copywriter's call to action: comment RAIO-X, or the bio's link.
      IG_CAPTION_CTA_LIVE ? IG_CAPTION_CTA : `\xC9 dono de bar? Mande o seu card\xE1pio: ${SITE}/para-bares`,
      CONAR,
      tags.join(" ")
    ].join("\n\n")
  );
}
function postTags(post, now = Date.now() / 1e3) {
  const fresh = (entry) => (entry.deal.verifiedAt ?? 0) >= now - TAG_FRESH_DAYS * 86400;
  const tags = [];
  const first = post.top[0];
  if (first && fresh(first)) tags.push({ slide: 1, venue: first.deal.venueId, x: 0.4, y: 0.62 });
  post.top.forEach((entry, index) => {
    if (fresh(entry))
      tags.push({ slide: 2, venue: entry.deal.venueId, x: 0.35, y: Math.min(0.9, 0.3 + index * 0.13) });
  });
  return tags;
}

// apps/site/src/logo.ts
var LOCKUP_DARK = {
  width: 6495,
  height: 1093,
  middle: 607,
  paths: [
    {
      fill: "#ffffff",
      d: "m424 808c-106 87-261 76-354-24c-93-100-93-255 0-355c93-100 248-110 354-24c6 5 10 13 10 20c1 8-2 16-8 21l-63 61c-10 9-24 11-35 3c-49-34-116-27-156 18c-40 45-40 112 0 157c40 45 107 53 156 18c11-7 25-6 35 3l63 61c6 5 9 13 8 21c0 8-4 15-10 20zm663-201c0 143-116 260-260 260c-22 0-43-3-64-8l-196 0l0-831c0-16 13-28 28-28l86 0c16 0 28 12 28 28l0 347c35-18 76-28 118-28c144 0 260 116 260 260zm-142 0c0-66-53-119-118-119c-65 0-118 53-118 119c0 65 53 118 118 118c65 0 118-53 118-118z"
    },
    {
      fill: "#ff6f59",
      d: "m1552 670c0 206-108 344-305 423c56-91 84-166 80-228c-96-14-169-96-169-195c0-109 88-197 197-197c109 0 197 88 197 197z"
    },
    {
      fill: "#ffffff",
      d: "m4743 607c0 98-82 177-183 177c-18 0-36-2-53-7l-129 0l0-396c0-38 30-69 68-69c38 0 68 31 68 69l0 53c15-3 30-5 46-5c101 0 183 79 183 178zm831 176q-36 0-64-13q-29-14-45-37q-16-23-16-53q0-47 39-74q38-27 102-37l76-12l0-5q0-20-7-30q-6-10-26-10q-14 0-22 5q-9 5-16 15q-6 8-17 8l-92 0q-7 0-11-4q-4-4-3-11q0-12 9-28q10-16 29-31q19-15 50-26q31-10 74-10q43 0 75 10q31 10 52 27q20 18 30 43q9 26 9 56l0 194q0 7-4 12q-5 5-13 5l-95 0q-7 0-12-5q-5-5-5-12l0-21q-9 13-23 23q-14 10-32 15q-19 6-42 6zm-1592 0q-36 0-64-13q-29-14-45-37q-16-23-16-53q0-47 38-74q39-27 103-37l75-12l0-5q0-20-6-30q-6-10-26-10q-14 0-22 5q-9 5-16 15q-6 8-17 8l-92 0q-7 0-11-4q-4-4-4-11q1-12 10-28q9-16 29-31q19-15 50-26q31-10 74-10q43 0 75 10q31 10 51 27q21 18 31 43q9 26 9 56l0 194q0 7-5 12q-5 5-12 5l-95 0q-7 0-12-5q-5-5-5-12l0-21q-9 13-23 23q-14 10-32 15q-19 6-42 6zm910 0q-36 0-64-13q-29-14-45-37q-16-23-16-53q0-47 39-74q38-27 102-37l76-12l0-5q0-20-7-30q-6-10-26-10q-14 0-22 5q-9 5-16 15q-6 8-17 8l-92 0q-7 0-11-4q-4-4-3-11q0-12 9-28q10-16 29-31q20-15 50-26q31-10 74-10q43 0 75 10q31 10 52 27q20 18 30 43q10 26 10 56l0 194q0 7-5 12q-5 5-13 5l-95 0q-7 0-12-5q-5-5-5-12l0-21q-9 13-23 23q-14 10-32 15q-19 6-42 6zm1377 0q-37 0-65-13q-28-14-44-37q-16-23-16-53q0-47 38-74q38-27 102-37l76-12l0-5q0-20-6-30q-7-10-27-10q-14 0-22 5q-8 5-15 15q-7 8-17 8l-93 0q-6 0-11-4q-4-4-3-11q0-12 10-28q9-16 28-31q20-15 50-26q31-10 74-10q44 0 75 10q32 10 52 27q20 18 30 43q10 26 10 56l0 194q0 7-5 12q-5 5-12 5l-95 0q-7 0-12-5q-5-5-5-12l0-21q-9 13-23 23q-14 10-33 15q-19 6-41 6zm-3873 0q-82 0-130-42q-48-42-49-129q0-3 0-7q0-4 0-6q2-54 24-92q22-38 61-57q40-20 93-20q61 0 100 24q40 24 59 63q20 40 20 89l0 18q0 7-5 12q-5 5-13 5l-202 0l0 4q1 16 5 28q4 12 13 19q9 7 23 7q8 0 14-3q6-2 10-6q5-5 8-8q6-7 10-8q3-2 11-2l103 0q6 0 10 4q5 4 4 10q-1 12-11 28q-11 17-32 33q-20 15-52 26q-32 10-74 10zm1063 0q-81 0-129-42q-48-42-49-129q0-3 0-7q0-4 0-6q1-54 23-92q23-38 62-57q40-20 93-20q61 0 100 24q40 24 59 63q19 40 19 89l0 18q0 7-5 12q-5 5-12 5l-202 0l0 4q1 16 5 28q4 12 13 19q9 7 22 7q9 0 15-3q6-2 10-6q5-5 8-8q6-7 10-8q3-2 11-2l102 0q7 0 11 4q4 4 4 10q-1 12-12 28q-10 17-31 33q-21 15-52 26q-32 10-75 10zm2583-6q-45 0-78-13q-34-13-53-43q-19-30-19-79l0-105l-51 0q-7 0-12-5q-5-5-5-12l0-66q0-7 5-12q5-5 12-5l51 0l0-107q0-8 5-13q5-5 12-5l96 0q7 0 12 5q5 5 5 13l0 107l82 0q7 0 12 5q5 5 5 12l0 66q0 7-5 12q-5 5-12 5l-82 0l0 94q0 19 7 29q8 11 25 11l55 0q7 0 12 5q5 5 5 12l0 72q0 7-5 12q-5 5-12 5l-67 0zm-4031 6q-49 0-88-17q-39-18-62-51q-24-34-26-82q-1-10-1-26q0-16 1-27q2-48 25-81q23-34 63-51q39-18 88-18q48 0 83 14q34 14 55 34q22 20 32 40q10 21 11 35q1 7-5 12q-5 5-12 5l-105 0q-8 0-12-4q-4-3-7-10q-6-14-15-21q-8-7-23-7q-20 0-30 14q-9 14-10 42q-1 27 0 46q1 29 11 42q9 13 29 13q17 0 24-7q8-7 14-21q3-6 7-10q4-3 12-3l105 0q7 0 12 5q6 5 5 12q-1 9-7 24q-6 16-19 33q-13 16-34 31q-21 15-51 25q-30 9-70 9zm1031-6q-10 0-15-5q-4-6-7-12l-125-303q-1-2-1-5q0-6 5-11q4-4 10-4l93 0q9 0 14 5q4 4 6 9l63 177l63-177q2-5 7-9q4-5 14-5l92 0q6 0 11 4q4 5 4 11q0 3-1 5l-124 303q-3 6-8 12q-4 5-14 5l-87 0zm579 124q-7 0-12-5q-5-5-5-12l0-66q0-7 5-12q5-5 12-5l31 0q13 0 20-5q8-5 11-14q3-10 3-21l0-307q0-7 5-12q5-5 12-5l102 0q7 0 12 5q5 5 5 12l0 310q0 46-19 76q-20 30-54 46q-35 15-81 15l-47 0zm-990-124q-7 0-12-5q-5-5-5-12l0-306q0-7 5-12q5-5 12-5l95 0q7 0 12 5q5 5 5 12l0 24q20-19 45-30q25-11 55-11l30 0q7 0 12 5q5 5 5 12l0 84q0 7-5 12q-5 5-12 5l-74 0q-23 0-35 13q-13 12-13 35l0 157q0 7-5 12q-5 5-12 5l-103 0zm2556 0q-7 0-12-5q-5-5-5-12l0-306q0-7 5-12q5-5 12-5l95 0q7 0 12 5q5 5 5 12l0 24q19-19 45-30q25-11 55-11l30 0q7 0 12 5q5 5 5 12l0 84q0 7-5 12q-5 5-12 5l-74 0q-23 0-36 13q-12 12-12 35l0 157q0 7-5 12q-5 5-12 5l-103 0zm-1483-383q-7 0-12-5q-5-5-5-12l0-69q0-7 5-12q5-5 12-5l100 0q7 0 12 5q5 5 5 12l0 69q0 7-5 12q-5 5-12 5l-100 0zm902 213c0-34-20-61-46-61c-25 0-46 27-46 61c0 33 21 60 46 60c26 0 46-27 46-60zm1006 91q16 0 29-7q12-7 19-21q7-15 7-36l0-5l-47 9q-24 5-34 13q-10 9-10 20q0 8 5 14q5 6 13 9q8 4 18 4zm-1592 0q16 0 29-7q12-7 19-21q7-15 7-36l0-5l-47 9q-24 5-34 13q-10 9-10 20q0 8 5 14q5 6 13 9q8 4 18 4zm910 0q16 0 29-7q12-7 19-21q7-15 7-36l0-5l-47 9q-24 5-34 13q-10 9-10 20q0 8 5 14q5 6 13 9q8 4 18 4zm1376 0q17 0 29-7q12-7 19-21q7-15 7-36l0-5l-47 9q-24 5-33 13q-10 9-10 20q0 8 4 14q5 6 13 9q8 4 18 4zm-2888-130l82 0l0-1q0-18-5-30q-5-13-14-19q-9-6-22-6q-13 0-22 6q-9 6-14 19q-5 12-5 30l0 1zm-1064 0l82 0l0-1q0-18-5-30q-4-13-14-19q-9-6-22-6q-13 0-22 6q-9 6-14 19q-5 12-5 30l0 1z"
    }
  ]
};

// tools/scripts/src/social/slides.ts
var WIDTH = 1080;
var HEIGHT = 1350;
var CONAR_TEXT = 32;
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
var LOGO_HEIGHT = 74;
var LOGO_CLEAR = Math.ceil(LOGO_HEIGHT / 4);
var FOOT_CLEAR = 16;
var HEADLINE_FLOOR = 56;
var MIN_HEADLINE = 48;
var LOGO = `<svg class="logo" viewBox="0 0 ${LOCKUP_DARK.width} ${LOCKUP_DARK.height}" role="img" aria-label="Cerveja Barata">${LOCKUP_DARK.paths.map(({ fill, d }) => `<path fill="${fill}" d="${d}"/>`).join("")}</svg>`;
function css(fonts2) {
  return `
@font-face { font-family: Rubik; font-weight: 300 900; src: url(data:font/woff2;base64,${fonts2.rubik}) format("woff2"); }
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: ${WIDTH}px; height: ${HEIGHT}px; }
body { font-family: Rubik, sans-serif; color: ${COLORS.white}; background: ${COLORS.primary}; -webkit-font-smoothing: antialiased; }
.slide { position: relative; width: ${WIDTH}px; height: ${HEIGHT}px; overflow: hidden; display: flex; flex-direction: column; padding: 64px 72px 0; }
.top { display: flex; align-items: center; justify-content: space-between; }
.logo { display: block; height: ${LOGO_HEIGHT}px; width: auto; }
.counter { font-weight: 700; font-size: 36px; letter-spacing: 0.06em; color: ${COLORS.sea}; font-variant-numeric: tabular-nums; }
.eyebrow { font-weight: 800; font-size: 36px; line-height: 1.15; letter-spacing: 0.1em; text-transform: uppercase; color: ${COLORS.sea}; }
/* Line height 1: an accented capital (\xC1, \xC3, \xC9) clears the line above. */
h1 { font-weight: 900; text-transform: uppercase; letter-spacing: -0.02em; line-height: 1; color: ${COLORS.accent}; text-wrap: balance; }
/* Centred between the logo's clear space and the footer; "safe": a block too tall starts under the logo instead of
   spilling up into it, so an overflow only runs down, where FIT_SCRIPT sees it. */
.main { flex: 1; min-height: 0; display: flex; flex-direction: column; justify-content: safe center; padding: ${LOGO_CLEAR}px 0 ${FOOT_CLEAR}px; }
.foot { margin: 0 -72px; padding: 24px 72px 30px; background: ${COLORS.deep}; text-align: center; }
.foot .site { font-weight: 800; font-size: 36px; line-height: 1.15; color: ${COLORS.white}; letter-spacing: 0.01em; }
.foot .conar { margin-top: 10px; font-weight: 600; font-size: ${CONAR_TEXT}px; color: ${COLORS.sea}; }
.card { background: ${COLORS.white}; color: ${COLORS.ink}; border-radius: 36px; }
.chip { display: inline-block; border-radius: 28px; padding: 4px 20px 5px; font-weight: 700; font-size: 36px; line-height: 1.15; }
.chip.hh { background: ${COLORS.primary}; color: ${COLORS.white}; }
.nowrap { white-space: nowrap; }
/* A phrase that moves to the next line whole ("Garrafa 600 ml", never "600" / "ml"), unless it is wider than a line. */
.part { display: inline-block; max-width: 100%; }
/* Cover */
.cover h1 { font-size: 112px; margin-top: 14px; }
.cover .hero { margin-top: 44px; padding: 40px 48px 42px; }
.cover .hero .label { font-weight: 800; font-size: 36px; line-height: 1.15; letter-spacing: 0.08em; text-transform: uppercase; color: ${COLORS.muted}; }
.cover .hero .price { font-weight: 900; font-size: 168px; line-height: 1; letter-spacing: -0.03em; margin-top: 10px; color: ${COLORS.ink}; font-variant-numeric: tabular-nums; }
.cover .hero .tags { margin-top: 18px; display: flex; flex-wrap: wrap; align-items: center; gap: 14px 16px; }
.cover .hero .litre { border-radius: 999px; background: ${COLORS.accent}; color: ${COLORS.ink}; padding: 8px 26px; font-weight: 900; font-size: 40px; line-height: 1.15; font-variant-numeric: tabular-nums; }
.cover .hero .what { margin-top: 24px; font-weight: 700; font-size: 38px; line-height: 1.2; }
.cover .hero .where { margin-top: 6px; font-weight: 500; font-size: 36px; line-height: 1.2; color: ${COLORS.muted}; }
.cover .swipe { margin-top: 36px; font-weight: 700; font-size: 36px; color: ${COLORS.sea}; text-align: right; }
/* Ranking */
.ranking h1 { font-size: 80px; margin-top: 10px; }
.ranking .sub { margin-top: 10px; font-weight: 500; font-size: 36px; line-height: 1.2; color: ${COLORS.sea}; }
.ranking .list { margin-top: 22px; display: flex; flex-direction: column; gap: 10px; }
.row { display: grid; grid-template-columns: 68px minmax(0, 1fr) auto; column-gap: 20px; row-gap: 10px; align-items: center; padding: 14px 24px; }
.row .rank { grid-row: 1; width: 68px; height: 68px; border-radius: 50%; background: ${COLORS.primary}; color: ${COLORS.white};
  display: flex; align-items: center; justify-content: center; font-weight: 900; font-size: 38px; font-variant-numeric: tabular-nums; }
.row.first .rank { background: ${COLORS.accent}; color: ${COLORS.ink}; }
.row .body { grid-column: 2; grid-row: 1; min-width: 0; overflow-wrap: break-word; }
/* The bar's name, then its bairro on the city's post (on the next line when they don't fit together). */
.row .venue { font-weight: 800; font-size: 38px; line-height: 1.08; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.row .venue .where { font-weight: 500; font-size: 36px; color: ${COLORS.muted}; }
.row .meta { margin-top: 4px; font-weight: 500; font-size: 36px; line-height: 1.15; color: ${COLORS.muted}; }
.row .prices { grid-column: 3; grid-row: 1; text-align: right; }
.row .price { font-weight: 900; font-size: 52px; line-height: 0.88; letter-spacing: -0.02em; white-space: nowrap; font-variant-numeric: tabular-nums; }
.row .litre { margin-top: 8px; display: inline-block; border-radius: 999px; background: ${COLORS.accent}; padding: 3px 16px; font-weight: 800; font-size: 36px; line-height: 1.05; white-space: nowrap; font-variant-numeric: tabular-nums; }
/* A happy hour: its days and hours under the bar and the price, across both (they can be long). */
.row .chip { grid-column: 2 / 4; grid-row: 2; justify-self: start; }
.row.timed .rank { grid-row: 1 / 3; }
/* A full slide (FIT_SCRIPT adds .tight): the blocks sit closer, so the headline keeps its size */
.ranking.tight h1, .ranking.tight .sub { margin-top: 6px; }
.ranking.tight .list { margin-top: 16px; gap: 8px; }
.ranking.tight .row { padding: 10px 24px; row-gap: 8px; }
.cover.tight .hero { margin-top: 32px; padding-top: 34px; padding-bottom: 36px; }
.cover.tight .swipe { margin-top: 26px; }
/* The last resort (.tighter): closer lines too, never smaller type */
.ranking.tighter .eyebrow { line-height: 1.05; }
.ranking.tighter .sub { line-height: 1.12; }
.ranking.tighter .list { margin-top: 12px; gap: 6px; }
.ranking.tighter .row { padding: 8px 22px; row-gap: 6px; }
.ranking.tighter .venue { line-height: 1.04; }
.ranking.tighter .meta { margin-top: 2px; line-height: 1.1; }
.ranking.tighter .row .chip { padding-top: 2px; padding-bottom: 3px; }
.cover.tighter .hero { margin-top: 24px; padding-top: 28px; padding-bottom: 30px; }
.cover.tighter .hero .what { margin-top: 16px; }
.cover.tighter .swipe { margin-top: 18px; }
/* Closing */
.closing h1 { font-size: 104px; }
.closing .url { margin-top: 40px; padding: 36px 48px; font-weight: 900; font-size: 54px; line-height: 1.15; word-break: break-word; }
.closing .url .path { color: ${COLORS.primary}; }
.closing .url .domain { color: ${COLORS.ink}; }
.closing .facts { margin-top: 36px; font-weight: 500; font-size: 36px; line-height: 1.35; color: ${COLORS.sea}; text-wrap: pretty; }
.closing .facts b { color: ${COLORS.white}; font-weight: 800; }
.closing .bars { margin-top: 36px; padding-top: 30px; border-top: 3px solid rgba(191, 224, 236, 0.35); font-size: 36px; line-height: 1.3; font-weight: 500; text-wrap: pretty; }
.closing .bars b { color: ${COLORS.accent}; font-weight: 800; font-size: 48px; }
`;
}
function page(fonts2, kind, index, total, body) {
  return `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><style>${css(fonts2)}</style></head><body>
<div class="slide ${kind}">
  <div class="top">${LOGO}<span class="counter">${index}/${total}</span></div>
  <div class="main">${body}</div>
  <div class="foot"><div class="site">${SITE}</div><div class="conar">${html(CONAR)}</div></div>
</div></body></html>`;
}
var kindChip = (entry) => entry.kind ? `<span class="chip hh">${html(entry.kind)}${entry.hours ? ` \xB7 ${html(entry.hours)}` : ""}</span>` : "";
var parts = (items) => items.filter((part) => Boolean(part)).map((part) => `<span class="part">${html(part)}</span>`).join(" \xB7 ");
function cover(post) {
  const first = post.top[0];
  if (!first) throw new Error(`${post.key}: no prices`);
  return `<div class="eyebrow">Semana de ${html(post.week)}</div>
<h1>${html(post.headline)}</h1>
<div class="card hero">
  <div class="label">N\xBA 1 em pre\xE7o por litro</div>
  <div class="price">${html(first.price)}</div>
  <div class="tags"><div class="litre">${html(first.perLitre.replace("/L", " o litro"))}</div>${kindChip(first)}</div>
  <div class="what">${html(first.beer)} \xB7 ${html(first.serving)}</div>
  <div class="where">${html(first.venue)}${first.bairro ? `, ${parts([first.bairro])}` : ""}</div>
</div>
<div class="swipe">Arraste para ver o top ${post.top.length} \u203A</div>`;
}
function ranking(post) {
  const rows = post.top.map((entry) => {
    const where = !post.bairro && entry.bairro ? `<span class="where">, ${parts([entry.bairro])}</span>` : "";
    const classes = ["card", "row", entry.rank === 1 ? "first" : "", entry.kind ? "timed" : ""];
    return `<div class="${classes.filter(Boolean).join(" ")}">
  <div class="rank">${entry.rank}</div>
  <div class="body"><div class="venue">${html(entry.venue)}${where}</div><div class="meta">${parts([entry.beer, entry.serving])}</div></div>
  <div class="prices"><div class="price">${html(entry.price)}</div><div class="litre">${html(entry.perLitre)}</div></div>${kindChip(entry)}
</div>`;
  }).join("\n");
  return `<div class="eyebrow">Semana de ${html(post.week)}</div>
<h1>Top ${post.top.length} ${html(inPlace(post.bairro?.name ?? post.city.name))}</h1>
<div class="sub">${parts(["Pelo pre\xE7o por litro", "s\xF3 bares", "o pre\xE7o mais baixo de cada bar"])}</div>
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
  const TOP = ${LOGO_CLEAR}, BOTTOM = ${FOOT_CLEAR}, FLOOR = ${HEADLINE_FLOOR}, MIN = ${MIN_HEADLINE};
  const slide = document.querySelector(".slide");
  const head = document.querySelector(".slide > .top");
  const main = document.querySelector(".main");
  const foot = document.querySelector(".foot");
  const h1 = main.querySelector("h1");
  const round = (value) => Math.round(value * 10) / 10;
  const measure = () => {
    const first = main.firstElementChild.getBoundingClientRect();
    const last = main.lastElementChild.getBoundingClientRect();
    return {
      top: round(first.top - head.getBoundingClientRect().bottom),
      bottom: round(foot.getBoundingClientRect().top - last.bottom),
      overflow: main.scrollHeight > main.clientHeight + 1,
    };
  };
  const clear = (m) => m.top >= TOP && m.bottom >= BOTTOM && !m.overflow;
  let m = measure();
  const start = h1 ? parseFloat(getComputedStyle(h1).fontSize) : 0;
  let size = start;
  const closer = (name) => {
    if (clear(m)) return false;
    slide.classList.add(name);
    m = measure();
    return true;
  };
  const shrink = (floor) => {
    while (h1 && !clear(m) && size > floor) {
      size = Math.max(floor, size - 4);
      h1.style.fontSize = size + "px";
      m = measure();
    }
  };
  // Room the closer lines freed goes back to the headline, as far as it still fits.
  const grow = () => {
    while (h1 && clear(m) && size + 4 <= start) {
      h1.style.fontSize = size + 4 + "px";
      const next = measure();
      if (!clear(next)) {
        h1.style.fontSize = size + "px";
        return;
      }
      size += 4;
      m = next;
    }
  };
  closer("tight");
  shrink(FLOOR);
  if (closer("tighter")) grow();
  shrink(MIN);
  let outside = 0;
  const margins = main.getBoundingClientRect();
  for (const element of main.querySelectorAll("*")) {
    const box = element.getBoundingClientRect();
    if (!box.width) continue;
    const card = element.parentElement && element.parentElement.closest(".card");
    const frame = card ? card.getBoundingClientRect() : margins;
    if (box.left < frame.left - 0.5 || box.right > frame.right + 0.5) outside++;
  }
  let clamped = 0;
  for (const name of main.querySelectorAll(".venue")) {
    if (name.scrollHeight > name.clientHeight + parseFloat(getComputedStyle(name).fontSize) / 2) clamped++;
  }
  return { fits: clear(m) && outside === 0, ...m, outside, clamped, headline: size };
})()`;
function slides(post, fonts2) {
  const bodies = [
    ["cover", cover(post)],
    ["ranking", ranking(post)],
    ["closing", closing(post)]
  ];
  return bodies.map(([kind, body], index) => page(fonts2, kind, index + 1, bodies.length, body));
}
var ALT_MAX = 1e3;
var inSentence = (text) => `${text.charAt(0).toLowerCase()}${text.slice(1)}`;
var priceSentence = (entry) => `${entry.beer} ${inSentence(entry.serving)}, ${entry.price} (${entry.perLitre.replace("/L", " o litro")}).${entry.kind ? ` ${entry.kind}${entry.hours ? `, ${entry.hours}` : ""}.` : ""}`;
function within(sentences) {
  const kept = [...sentences];
  let text = kept.map(([sentence]) => sentence).join(" ");
  while (text.length > ALT_MAX) {
    const index = kept.findLastIndex(([, optional]) => optional);
    if (index < 0) return `${text.slice(0, ALT_MAX - 1).replace(/\s+\S*$/, "")}\u2026`;
    kept.splice(index, 1);
    text = kept.map(([sentence]) => sentence).join(" ");
  }
  return text;
}
function altTexts(post) {
  const first = post.top[0];
  if (!first) throw new Error(`${post.key}: no prices`);
  const total = 3;
  const where = inPlace(post.bairro?.name ?? post.city.name);
  const head = (index) => `Slide ${index} de ${total}.`;
  const warnings = `${CONAR.split(" \xB7 ").join(". ")}.`;
  const footer = `${SITE}. ${warnings}`;
  const cover2 = [
    [head(1), false],
    [`${post.headline}, semana de ${post.week}.`, false],
    [`N\xBA 1 em pre\xE7o por litro: ${first.venue}${first.bairro ? `, ${first.bairro}` : ""}.`, false],
    [priceSentence(first), false],
    [`Arraste para ver o top ${post.top.length}.`, true],
    [footer, true]
  ];
  const ranking2 = [
    [head(2), false],
    [`Top ${post.top.length} ${where}, semana de ${post.week}.`, false],
    ["Pelo pre\xE7o por litro, s\xF3 bares, o pre\xE7o mais baixo de cada bar.", true],
    ...post.top.map((entry) => [
      `${entry.rank}\xBA lugar: ${entry.venue}${!post.bairro && entry.bairro ? `, ${entry.bairro}` : ""}. ${priceSentence(entry)}`,
      false
    ]),
    [footer, true]
  ];
  const closing2 = [
    [head(3), false],
    [`Veja o mapa completo em ${post.url}.`, false],
    [
      `${post.bars} bares com pre\xE7o ${where}, na lista e no mapa, do mais barato ao mais caro por litro. Dados de ${post.updated}.`,
      false
    ],
    [`Siga ${INSTAGRAM} para o ranking de toda semana.`, false],
    [`\xC9 dono de bar? Mande o seu card\xE1pio: ${SITE}/para-bares.`, false],
    // The address is in the sentences above already.
    [warnings, true]
  ];
  return [cover2, ranking2, closing2].map((sentences) => plain2(within(sentences)));
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
  const read = (file) => readFileSync3(require2.resolve(file)).toString("base64");
  return { rubik: read("@fontsource-variable/rubik/files/rubik-latin-wght-normal.woff2") };
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
      batch: { type: "string" },
      archive: { type: "string" }
    }
  });
  if (!values.out) throw new Error("--out <dir> is required");
  const target = values.upload ? uploadTarget() : null;
  const batch = values.batch ?? batchName("manual", /* @__PURE__ */ new Date());
  const city = values.city ?? "salvador";
  const data = values.snapshot ? readSnapshot(values.snapshot) : await liveData(city);
  const archive = values.archive ? freezeArchive(values.archive, data, city) : null;
  if (archive?.added) console.log(`${values.archive}: froze this Friday's top 10`);
  const posts = buildPosts(data, {
    city,
    bairros: values.bairros?.split(",").map((slug) => slug.trim()),
    maxBairros: values.max ? Number(values.max) : void 0,
    frozen: archive?.frozen ?? null
  });
  mkdirSync2(values.out, { recursive: true });
  const { chromium } = await import("@playwright/test");
  const browser = await chromium.launch();
  try {
    const page2 = await browser.newPage({ viewport: { width: WIDTH, height: HEIGHT }, deviceScaleFactor: 1 });
    const typefaces = fonts();
    for (const [index, post] of posts.entries()) {
      const stem = `post-${index + 1}-${post.key}`;
      const files = [];
      const alts = altTexts(post);
      let fitted = true;
      for (const [slide, html2] of slides(post, typefaces).entries()) {
        await page2.setContent(html2, { waitUntil: "load" });
        await page2.evaluate("document.fonts.ready");
        const fit = await page2.evaluate(FIT_SCRIPT);
        const file = join2(values.out, `${stem}-${slide + 1}.jpg`);
        writeFileSync2(file, await toJpeg(await page2.screenshot({ type: "png" })));
        writeFileSync2(join2(values.out, `${stem}-alt-${slide + 1}.txt`), `${alts[slide] ?? ""}
`);
        files.push(file);
        if (fit.clamped) console.warn(`${file}: ${fit.clamped} bar name(s) cut short; check them`);
        if (!fit.fits) {
          console.error(
            `${file}: the text doesn't fit (${fit.top} px under the logo, ${fit.bottom} px over the footer, ${fit.outside} sticking out); check it before posting`
          );
          process.exitCode = 1;
          fitted = false;
        }
      }
      const text = caption(post);
      const altBlock = alts.join("\n\n");
      writeFileSync2(
        join2(values.out, `caption-${index + 1}-${post.key}.txt`),
        `${text}

---
Alt text per slide (draft for the copywriter's review; not part of the caption):

${altBlock}
`
      );
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
            slides: files.map((file) => readFileSync3(file).toString("base64")),
            tags: postTags(post)
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
