/** Validate the versioned, already-public website inputs without network access.
 * Never import a private database or copy arbitrary input properties to the site.
 */
const sentiments = new Set(['positive', 'negative', 'neutral']);
const knownAspects = new Set(['teaching', 'career', 'dormitory', 'infrastructure', 'administration', 'student_life', 'finance']);

function fail(label, message) {
  throw new Error(`${label}: ${message}`);
}

function object(value, allowed, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(label, 'ожидается объект');
  if (Object.keys(value).some(key => !allowed.includes(key))) fail(label, 'неподдерживаемые поля; удалите приватные и служебные сведения');
}

function string(value, label, maximum = 5000) {
  if (typeof value !== 'string' || !value.trim() || Array.from(value).length > maximum) {
    fail(label, `ожидается непустая строка до ${maximum} символов`);
  }
  return value.trim();
}

function boolean(value, label) {
  if (typeof value !== 'boolean') fail(label, 'требуется явное true или false');
  return value;
}

function identifier(value, label) {
  if (!Number.isSafeInteger(value) || value < 1) fail(label, 'ожидается положительное безопасное целое число');
  return value;
}

function https(value, label) {
  const text = string(value, label, 2048);
  if (/[\u0000-\u0020\u007f\\]/u.test(text)) fail(label, 'недопустимые символы в HTTPS URL');
  let url;
  try { url = new URL(text); } catch { fail(label, 'неверный HTTPS URL'); }
  if (url.protocol !== 'https:' || !url.hostname || url.username || url.password) fail(label, 'допустим только HTTPS URL без учётных данных');
  return url;
}

function iso(value, label) {
  if (typeof value !== 'string') fail(label, 'ожидается дата ISO 8601');
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(Z|[+-]\d{2}:\d{2}))?$/.exec(value);
  if (!match) fail(label, 'ожидается ГГГГ-ММ-ДД или время ISO 8601 с часовым поясом');
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, offset] = match;
  const year = Number(yearText), month = Number(monthText), day = Number(dayText);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const lengths = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (!year || month < 1 || month > 12 || day < 1 || day > lengths[month - 1]) fail(label, 'неверная календарная дата');
  if (hourText !== undefined && (Number(hourText) > 23 || Number(minuteText) > 59 || Number(secondText) > 59)) fail(label, 'неверное время');
  if (offset && offset !== 'Z' && (Number(offset.slice(1, 3)) > 23 || Number(offset.slice(4, 6)) > 59)) fail(label, 'неверный часовой пояс');
  const parsed = new Date(hourText === undefined ? `${value}T00:00:00Z` : value);
  if (!Number.isFinite(parsed.getTime())) fail(label, 'дата вне допустимого диапазона');
  const result = parsed.toISOString().replace(/\.\d{3}Z$/, 'Z');
  if (!/^\d{4}-/u.test(result) || result.startsWith('0000-')) fail(label, 'дата вне допустимого диапазона');
  return result;
}

function normalizeText(value) {
  return (value.normalize('NFKC').toLowerCase().replace(/ё/g, 'е').match(/[a-zа-я0-9ı]+/gu) || []).join(' ');
}

function sentiment(value, label) {
  if (!sentiments.has(value)) fail(label, 'используйте positive, negative или neutral');
  return value;
}

function catalogInput(catalog) {
  object(catalog, ['schema_version', 'settings', 'universities'], 'catalog');
  if (catalog.schema_version !== 1) fail('catalog.schema_version', 'поддерживается версия 1');
  object(catalog.settings, ['min_reviews', 'smoothing', 'min_topic_reviews'], 'catalog.settings');
  const minimum = identifier(catalog.settings.min_reviews, 'catalog.settings.min_reviews');
  const smoothing = catalog.settings.smoothing;
  if (typeof smoothing !== 'number' || !Number.isFinite(smoothing) || smoothing < 0) fail('catalog.settings.smoothing', 'ожидается конечное неотрицательное число');
  if (catalog.settings.min_topic_reviews !== 3) fail('catalog.settings.min_topic_reviews', 'текущий алгоритм использует порог 3');
  if (!Array.isArray(catalog.universities) || !catalog.universities.length) fail('catalog.universities', 'ожидается непустой список');
  const ids = new Set();
  const universities = catalog.universities.map((entry, index) => {
    const label = `catalog.universities[${index}]`;
    object(entry, ['id', 'full_name', 'short_name', 'city', 'region', 'official_url', 'official_url_live_verified'], label);
    const id = string(entry.id, `${label}.id`, 80);
    if (!/^[a-z][a-z0-9_-]*$/.test(id) || ['constructor', 'prototype', '__proto__'].includes(id) || ids.has(id)) fail(`${label}.id`, 'неверный или повторный ID вуза');
    ids.add(id);
    return {
      id, full_name: string(entry.full_name, `${label}.full_name`, 400),
      short_name: string(entry.short_name, `${label}.short_name`, 200),
      city: string(entry.city, `${label}.city`, 200), region: string(entry.region, `${label}.region`, 200),
      official_url: https(entry.official_url, `${label}.official_url`).href,
      official_url_live_verified: entry.official_url_live_verified === undefined ? false : boolean(entry.official_url_live_verified, `${label}.official_url_live_verified`),
    };
  });
  return {universities, minimum, smoothing};
}

function rulesInput(rules) {
  object(rules, ['version', 'aspect_labels', 'aspect_patterns', 'positive_pattern', 'negative_pattern', 'stopwords'], 'rules');
  object(rules.aspect_labels, [...knownAspects], 'rules.aspect_labels');
  const names = Object.keys(rules.aspect_labels);
  if (!names.length) fail('rules.aspect_labels', 'ожидается хотя бы одна тема');
  object(rules.aspect_patterns, names, 'rules.aspect_patterns');
  const labels = {}, patterns = {};
  const pattern = (value, label) => {
    const source = string(value, label, 10000);
    try { new RegExp(source.replace(/\\w/g, '[\\p{L}\\p{N}_]'), 'iu'); }
    catch { fail(label, 'неверное регулярное выражение для браузерного анализатора'); }
    return source;
  };
  for (const name of names) {
    labels[name] = string(rules.aspect_labels[name], `rules.aspect_labels.${name}`, 200);
    patterns[name] = pattern(rules.aspect_patterns[name], `rules.aspect_patterns.${name}`);
  }
  if (!Array.isArray(rules.stopwords) || rules.stopwords.length > 10000) fail('rules.stopwords', 'ожидается список стоп-слов');
  return {
    version: string(rules.version, 'rules.version', 100), aspect_labels: labels, aspect_patterns: patterns,
    positive_pattern: pattern(rules.positive_pattern, 'rules.positive_pattern'),
    negative_pattern: pattern(rules.negative_pattern, 'rules.negative_pattern'),
    stopwords: rules.stopwords.map((word, index) => string(word, `rules.stopwords[${index}]`, 100)),
  };
}

function reviewInputs(inputs, universities, rules) {
  if (!Array.isArray(inputs)) fail('reviews', 'ожидается список публичных отзывов');
  const universityIndex = new Map(universities.map(university => [university.id, university]));
  const ids = new Set(), textKeys = new Set(), externalKeys = new Set();
  const sources = new Map(), sourceBindings = new Map(), sourcePages = new Map();
  const reviews = inputs.map((entry, index) => {
    const label = `reviews[${index}]`;
    object(entry, ['id', 'university_id', 'source', 'source_url', 'source_review_id', 'text', 'published_at',
      'collected_at', 'original_score', 'sentiment', 'approved', 'verified', 'personal_data_removed', 'display_allowed', 'aspects'], label);
    const id = identifier(entry.id, `${label}.id`);
    if (ids.has(id)) fail(`${label}.id`, 'повторный ID отзыва');
    ids.add(id);
    const university = universityIndex.get(entry.university_id);
    if (!university) fail(`${label}.university_id`, 'неизвестный вуз');
    if (entry.verified !== true) fail(`${label}.verified`, 'нужна ручная проверка источника и принадлежности вузу');
    if (entry.personal_data_removed !== true) fail(`${label}.personal_data_removed`, 'нужно подтвердить удаление персональных данных');
    const displayAllowed = boolean(entry.display_allowed, `${label}.display_allowed`);
    const approved = boolean(entry.approved, `${label}.approved`);
    let text = null;
    if (displayAllowed) {
      if (typeof entry.text !== 'string' || !entry.text.trim() || Array.from(entry.text).length > 5000) fail(`${label}.text`, 'ожидается публичный текст от 1 до 5000 символов');
      // Keep source quotes verbatim; normalize only the duplicate-detection key.
      text = entry.text;
      const normalized = normalizeText(text);
      if (!normalized) fail(`${label}.text`, 'в тексте нужны буквы или цифры');
      const textKey = JSON.stringify([university.id, normalized]);
      if (textKeys.has(textKey)) fail(`${label}.text`, 'повторный нормализованный текст для того же вуза');
      textKeys.add(textKey);
    } else if (entry.text !== null) {
      fail(`${label}.text`, 'при display_allowed=false текст должен быть null ещё до сохранения в Git');
    }
    object(entry.source, ['id', 'platform', 'url', 'terms_checked_at'], `${label}.source`);
    const sourceUrl = https(entry.source_url, `${label}.source_url`);
    const platformUrl = https(entry.source.url, `${label}.source.url`);
    platformUrl.hash = '';
    if (sourceUrl.hostname !== platformUrl.hostname) fail(`${label}.source_url`, 'ссылка на отзыв должна относиться к площадке источника');
    const source = {
      id: identifier(entry.source.id, `${label}.source.id`),
      platform: string(entry.source.platform, `${label}.source.platform`, 120),
      url: platformUrl.href, terms_checked_at: iso(entry.source.terms_checked_at, `${label}.source.terms_checked_at`),
    };
    const binding = source.url;
    const previousSource = sources.get(source.id);
    if (previousSource && (previousSource.platform !== source.platform || previousSource.url !== source.url
        || previousSource.terms_checked_at !== source.terms_checked_at)) fail(`${label}.source`, 'один ID источника должен иметь одинаковые площадку, URL и дату проверки');
    if (sourceBindings.has(binding) && sourceBindings.get(binding) !== source.id) fail(`${label}.source.id`, 'площадка с этим URL уже использует другой ID; переименование площадки не создаёт новый источник');
    sources.set(source.id, source);
    sourceBindings.set(binding, source.id);
    const externalId = entry.source_review_id === undefined || entry.source_review_id === null ? null
      : string(entry.source_review_id, `${label}.source_review_id`, 200);
    if (externalId !== null) {
      const externalKey = JSON.stringify([source.url, externalId]);
      if (externalKeys.has(externalKey)) fail(`${label}.source_review_id`, 'эта исходная публикация уже учтена; нельзя менять её вуз или повторно учитывать отредактированный текст');
      externalKeys.add(externalKey);
    }
    // Fragments cannot be used to manufacture distinct physical publications.
    const pageUrl = new URL(sourceUrl);
    pageUrl.hash = '';
    const pageKey = pageUrl.href;
    const previousIds = sourcePages.get(pageKey) || [];
    if (previousIds.length && (externalId === null || previousIds.some(value => value === null || value === externalId))) {
      fail(`${label}.source_url`, 'исходная ссылка уже учтена; общая страница допускается только с разными стабильными source_review_id');
    }
    previousIds.push(externalId);
    sourcePages.set(pageKey, previousIds);
    const publishedAt = iso(entry.published_at, `${label}.published_at`);
    const collectedAt = iso(entry.collected_at, `${label}.collected_at`);
    if (collectedAt < publishedAt) fail(`${label}.collected_at`, 'дата сбора не может быть раньше публикации');
    const originalScore = entry.original_score === undefined ? null : entry.original_score;
    if (originalScore !== null && (typeof originalScore !== 'number' || !Number.isFinite(originalScore))) fail(`${label}.original_score`, 'ожидается конечное число или null');
    const inputAspects = entry.aspects === undefined ? [] : entry.aspects;
    if (!Array.isArray(inputAspects)) fail(`${label}.aspects`, 'ожидается список');
    const aspectIds = new Set();
    const aspects = inputAspects.map((aspect, position) => {
      const aspectLabel = `${label}.aspects[${position}]`;
      object(aspect, ['aspect', 'sentiment', 'approved', 'context', 'confidence'], aspectLabel);
      if (!Object.hasOwn(rules.aspect_labels, aspect.aspect) || aspectIds.has(aspect.aspect)) fail(`${aspectLabel}.aspect`, 'неизвестная или повторная тема');
      aspectIds.add(aspect.aspect);
      const aspectApproved = boolean(aspect.approved, `${aspectLabel}.approved`);
      if (aspectApproved && !approved) fail(`${aspectLabel}.approved`, 'подтверждённая тема требует подтверждённого отзыва');
      if (aspect.confidence !== undefined && aspect.confidence !== null) fail(`${aspectLabel}.confidence`, 'ручные метки не имеют измеренной вероятности; используйте null');
      let context = null;
      if (displayAllowed) {
        if (typeof aspect.context !== 'string' || Array.from(aspect.context).length > 5000 || (aspect.context && !text.includes(aspect.context))) fail(`${aspectLabel}.context`, 'контекст должен быть точной цитатой из публичного текста или пустой строкой');
        context = aspect.context;
      } else if (aspect.context !== null) {
        fail(`${aspectLabel}.context`, 'при display_allowed=false контекст должен быть null ещё до сохранения в Git');
      }
      return {aspect: aspect.aspect, sentiment: sentiment(aspect.sentiment, `${aspectLabel}.sentiment`), approved: aspectApproved, context, confidence: null};
    }).sort((left, right) => left.aspect < right.aspect ? -1 : left.aspect > right.aspect ? 1 : 0);
    return {
      id, university_id: university.id, full_name: university.full_name, short_name: university.short_name,
      source: {id: source.id, platform: source.platform, url: source.url, terms_checked_at: source.terms_checked_at},
      source_url: sourceUrl.href, source_review_id: externalId, text, published_at: publishedAt, collected_at: collectedAt,
      original_score: originalScore, sentiment: sentiment(entry.sentiment, `${label}.sentiment`), approved,
      verified: true, personal_data_removed: true, display_allowed: displayAllowed, aspects,
      needs_review: !approved, label_origin: 'manual', model_version: 'manual-labels-v1', confidence: null, manual_reviewed: approved,
    };
  });
  const sourceList = [...sources.values()].map(source => ({
    id: source.id, platform: source.platform, url: source.url, terms_checked_at: source.terms_checked_at,
    review_count: reviews.filter(review => review.source.id === source.id).length,
  })).sort((left, right) => left.platform < right.platform ? -1 : left.platform > right.platform ? 1 : left.id - right.id);
  return {reviews, sourceList};
}

/** Return a validated skeleton; the shared browser API computes ratings/details. */
export function prepareSnapshot(catalog, reviewRecords, ruleDefinitions, options = {}) {
  const {universities, minimum, smoothing} = catalogInput(catalog);
  const rules = rulesInput(ruleDefinitions);
  const {reviews, sourceList} = reviewInputs(reviewRecords, universities, rules);
  const basePath = options.basePath ?? '/Baknin_Project/';
  if (typeof basePath !== 'string' || !basePath.startsWith('/') || !basePath.endsWith('/') || basePath.startsWith('//')
      || /[?#\\\u0000-\u0020\u007f]/u.test(basePath) || basePath.split('/').some(part => part === '.' || part === '..')) fail('basePath', 'нужен абсолютный путь сайта с завершающим /');
  const exportedAt = iso(options.exportedAt ?? new Date().toISOString(), 'exportedAt');
  const approvedCount = reviews.filter(review => review.approved).length;
  const counts = {universities: universities.length, reviews: reviews.length, approved: approvedCount, pending_moderation: reviews.length - approvedCount, sources: sourceList.length};
  const modelVersion = `${rules.version}-browser`;
  const parameterStatus = 'Пилотные параметры; калибровка на реальном корпусе ещё не выполнена.';
  const collectionDates = reviews.map(review => review.collected_at).sort();
  return {
    format: 1, exported_at: exportedAt, base_path: basePath, rules,
    universities: {items: universities, regions: [...new Set(universities.map(university => university.region))].sort(),
      meta: {min_reviews: minimum, smoothing, total: universities.length, parameter_status: parameterStatus}},
    reviews, details: {},
    methodology: {
      version: 'public-data-v1', timezone: 'Europe/Moscow', counts, stats: {...counts}, sources: sourceList, last_updated: collectionDates.at(-1) ?? null,
      requires_corpus: reviews.length === 0, models: reviews.length ? [{model_version: 'manual-labels-v1', predictions: 0, reviews: reviews.length}] : [],
      min_reviews: minimum, smoothing, min_topic_reviews: 3,
      score_formula: `100 × (P + ${smoothing} × p₀) / (P + N + U + ${smoothing})`,
      positive_percent_formula: '100 × P / (P + N + U)', baseline_scope: 'Все проверенные отзывы корпуса до применения фильтров',
      parameter_status: parameterStatus, engine: 'browser_rules', model_version: modelVersion,
      metrics_available: false, metrics: null, server_api_available: false, deployment: 'github_pages', snapshot: true, exported_at: exportedAt,
      aspects: Object.entries(rules.aspect_labels).map(([id, label]) => ({id, label})),
      sections: [
        {title: 'Источники', text: 'Публикуются только реальные отзывы с вручную проверенными источником, принадлежностью вузу и правом показа. Персональные данные удалены; тексты без разрешения заранее заменены на null.'},
        {title: 'Тональность и проверка', text: 'Метки корпуса проверяет человек. В рейтинг входят только approved=true. Словарный анализ нового текста выполняется локально в браузере, не имеет калиброванной уверенности и требует ручной проверки.'},
        {title: 'Рейтинг', text: `P — положительные, N — отрицательные, U — нейтральные проверенные отзывы. Среднее p₀ считается до фильтров. Вес сглаживания — ${smoothing}; при менее чем ${minimum} отзывах место и оценка не публикуются.`},
        {title: 'Аспекты', text: 'Проверенная тема учитывается один раз на отзыв. Сильная или слабая сторона требует не менее трёх подтверждений соответствующей тональности. Непроверенные темы исключаются.'},
        {title: 'Ограничения', text: 'Корпус пока не содержит реальных отзывов, если показано пустое состояние. Отзывы не являются репрезентативным опросом и не устанавливают объективное качество образования; размер выборки не устраняет систематическое смещение.'},
      ],
    },
    health: {status: 'ok', database: 'public_snapshot', model: 'ready', engine: 'browser_rules', model_version: modelVersion,
      ml_trained: false, model_diagnostic: null, deployment: 'github_pages', exported_at: exportedAt,
      limitation: 'Публичные данные обновляются через GitHub. Анализ работает локально; обученная модель не подключена.'},
  };
}
