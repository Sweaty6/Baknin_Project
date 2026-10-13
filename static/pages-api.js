/* Read-only public snapshot API for GitHub Pages. Prediction runs in this browser. */
(function (global) {
  "use strict";

  let snapshotPromise;
  const maxInteger = 9223372036854775807n;
  const sentiments = new Set(["positive", "negative", "neutral"]);
  const aggregateKeys = new Set([
    "reviews_total", "eligible_reviews", "pending_reviews", "positive", "negative", "neutral",
    "positive_percent", "smoothed_score", "rank", "small_sample", "eligible", "status", "reliability",
    "strengths", "weaknesses", "baseline_positive_fraction", "period_start", "period_end",
    "last_collected_at", "pending_percent",
  ]);
  const clone = value => JSON.parse(JSON.stringify(value));
  const casefold = value => String(value || "").toLowerCase().replace(/ß/g, "ss").replace(/ς/g, "σ");
  const compare = (left, right) => left < right ? -1 : left > right ? 1 : 0;
  const approved = value => value === true || value === 1;
  const eligible = review => approved(review.approved) && sentiments.has(review.sentiment);

  async function snapshot() {
    if (!snapshotPromise) {
      const configuredBase = global.document?.querySelector('meta[name="baknin-base"]')?.content || "/";
      const base = configuredBase.endsWith("/") ? configuredBase : configuredBase + "/";
      snapshotPromise = (async () => {
        const response = await global.fetch(base + "data/site.json", { credentials: "same-origin" });
        if (!response.ok) throw new Error("Не удалось загрузить опубликованные данные сайта. Попробуйте позже.");
        const data = await response.json();
        if (data.format !== 1 || !Array.isArray(data.universities?.items) || !Array.isArray(data.reviews)
            || !data.rules || !data.details || !data.methodology) {
          throw new Error("Формат опубликованных данных не поддерживается. Обновите страницу после публикации сайта.");
        }
        return data;
      })().catch(error => {
        snapshotPromise = undefined;
        if (error instanceof SyntaxError) throw new Error("Опубликованные данные повреждены. Попробуйте обновить страницу позже.");
        if (error instanceof TypeError) throw new Error("Не удалось загрузить данные сайта. Проверьте соединение и попробуйте снова.");
        throw error;
      });
    }
    return snapshotPromise;
  }

  function integer(value, name, minimum, maximum = maxInteger) {
    if (!/^[+-]?\d+$/.test(String(value).trim())) throw new Error(name + " должен быть целым числом");
    const result = BigInt(String(value).trim());
    if (result < BigInt(minimum) || result > maximum) throw new Error(name + " выходит за поддерживаемый диапазон");
    return result;
  }

  function dateFilter(value, name) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new Error(name + ": неверная дата, используйте ГГГГ-ММ-ДД");
    const [year, month, day] = value.split("-").map(Number);
    const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    const lengths = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    if (year < 1 || month < 1 || month > 12 || day < 1 || day > lengths[month - 1]) {
      throw new Error(name + ": неверная дата");
    }
    return value;
  }

  function filters(search) {
    const result = {};
    for (const name of ["q", "region", "sentiment", "aspect", "university_id"]) {
      const value = (search.get(name) || (name === "university_id" ? search.get("uni_id") : "") || "").trim();
      if (value) {
        if (Array.from(value).length > 200) throw new Error("Слишком длинный параметр фильтра");
        result[name] = value;
      }
    }
    if (result.sentiment && !sentiments.has(result.sentiment)) throw new Error("Неизвестная тональность");
    if (search.get("source_id")) result.source_id = integer(search.get("source_id"), "source_id", 1).toString();
    for (const name of ["date_from", "date_to"]) {
      if (search.get(name)) result[name] = dateFilter(search.get(name), name);
    }
    if (result.date_from && result.date_to && result.date_from > result.date_to) {
      throw new Error("Начальная дата не может быть позже конечной");
    }
    return result;
  }

  function matchingReviews(data, selected, searchText = true) {
    const universities = new Map(data.universities.items.map(university => [university.id, university]));
    const query = selected.q ? casefold(selected.q).trim() : null;
    return data.reviews.filter(review => {
      const university = universities.get(review.university_id) || {};
      if (selected.university_id && review.university_id !== selected.university_id) return false;
      if (selected.region && university.region !== selected.region) return false;
      if (selected.sentiment && review.sentiment !== selected.sentiment) return false;
      if (selected.source_id && String(review.source?.id) !== selected.source_id) return false;
      if (selected.aspect && !(review.aspects || []).some(aspect => aspect.aspect === selected.aspect)) return false;
      const publishedDay = String(review.published_at || "").slice(0, 10);
      if (selected.date_from && publishedDay < selected.date_from) return false;
      if (selected.date_to && publishedDay > selected.date_to) return false;
      if (searchText && query) {
        const publicText = approved(review.display_allowed) ? review.text : null;
        if (![publicText, university.full_name, university.short_name].some(value => casefold(value).includes(query))) return false;
      }
      return true;
    });
  }

  function reviewOrder(left, right) {
    return compare(right.published_at, left.published_at) || compare(Number(right.id), Number(left.id));
  }

  function topics(reviews) {
    const groups = new Map();
    for (const review of [...reviews].sort(reviewOrder)) {
      for (const aspect of review.aspects || []) {
        if (!approved(aspect.approved)) continue;
        if (!groups.has(aspect.aspect)) groups.set(aspect.aspect, []);
        groups.get(aspect.aspect).push({ review, aspect });
      }
    }
    return [...groups].sort(([left], [right]) => compare(left, right)).map(([aspect, rows]) => ({
      aspect, count: rows.length,
      ...Object.fromEntries(["positive", "negative", "neutral", "uncertain"].map(sentiment => [
        sentiment, rows.filter(row => row.aspect.sentiment === sentiment).length,
      ])),
      evidence: rows.slice(0, 5).map(({ review, aspect: row }) => ({
        review_id: review.id, text: approved(review.display_allowed) ? row.context : null,
        source_url: review.source_url, sentiment: row.sentiment,
      })),
    }));
  }

  function ranking(data, selected) {
    const minimum = data.universities.meta?.min_reviews ?? data.methodology.min_reviews ?? 10;
    const smoothing = data.universities.meta?.smoothing ?? data.methodology.smoothing ?? 10;
    const allEligible = data.reviews.filter(eligible);
    const baseline = allEligible.length ? allEligible.filter(review => review.sentiment === "positive").length / allEligible.length : 0;
    const allRows = matchingReviews(data, selected, false);
    const query = selected.q ? casefold(selected.q).trim() : null;
    const results = data.universities.items.filter(university => (
      (!selected.university_id || university.id === selected.university_id)
      && (!selected.region || university.region === selected.region)
      && (!query || casefold([university.full_name, university.short_name, university.city, university.region].join(" ")).includes(query))
    )).map(university => {
      const rows = allRows.filter(review => review.university_id === university.id);
      const validRows = rows.filter(eligible);
      const count = validRows.length;
      const small = count < minimum;
      const counts = Object.fromEntries([...sentiments].map(sentiment => [sentiment, validRows.filter(review => review.sentiment === sentiment).length]));
      const signals = topics(validRows);
      const publications = rows.map(review => review.published_at).sort(compare);
      const collections = rows.map(review => review.collected_at).sort(compare);
      const identity = Object.fromEntries(Object.entries(university).filter(([key]) => !aggregateKeys.has(key)));
      return {
        ...identity, reviews_total: rows.length, eligible_reviews: count, pending_reviews: rows.length - count,
        ...counts, positive_percent: count ? 100 * counts.positive / count : null,
        smoothed_score: small ? null : 100 * (counts.positive + smoothing * baseline) / (count + smoothing),
        rank: null, small_sample: small, eligible: !small,
        status: !rows.length ? "no_data" : small ? "insufficient" : "ranked",
        reliability: small ? "insufficient" : count < 30 ? "limited" : count < 100 ? "moderate" : "high",
        strengths: signals.filter(topic => topic.count >= 3 && topic.positive > topic.negative && topic.positive >= 3),
        weaknesses: signals.filter(topic => topic.count >= 3 && topic.negative > topic.positive && topic.negative >= 3),
        baseline_positive_fraction: allEligible.length ? baseline : null,
        period_start: publications[0] ?? null, period_end: publications.at(-1) ?? null,
        last_collected_at: collections.at(-1) ?? null,
        pending_percent: rows.length ? 100 * (rows.length - count) / rows.length : null,
      };
    });
    results.sort((left, right) => (
      Number(left.smoothed_score === null) - Number(right.smoothed_score === null)
      || (right.smoothed_score || 0) - (left.smoothed_score || 0)
      || compare(casefold(left.full_name), casefold(right.full_name))
    ));
    let previousScore = null, previousRank = null;
    results.forEach((university, index) => {
      if (university.smoothed_score === null) return;
      const score = university.smoothed_score;
      if (previousScore === null || Math.abs(score - previousScore) > Math.max(1e-12, 1e-9 * Math.max(Math.abs(score), Math.abs(previousScore)))) {
        previousRank = index + 1;
      }
      university.rank = previousRank;
      previousScore = score;
    });
    return {
      items: results, regions: data.universities.regions,
      meta: { ...data.universities.meta, total: results.length, exported_at: data.exported_at },
    };
  }

  function methodology(data) {
    const result = {
      ...data.methodology, engine: "browser_rules", model_version: data.rules.version + "-browser",
      metrics_available: false, metrics: null, exported_at: data.exported_at,
      deployment: "github_pages", snapshot: true,
    };
    // Nested descriptors must not suggest that Pages hosts a trained model.
    for (const key of ["analyzer", "model"]) if (result[key]) result[key] = {
      engine: result.engine, model_version: result.model_version, confidence: null,
    };
    return result;
  }

  async function request(path, options = {}) {
    let url;
    try { url = new URL(path, "https://baknin.invalid"); }
    catch { throw new Error("Некорректный адрес запроса"); }
    if (url.origin !== "https://baknin.invalid") throw new Error("Неизвестный адрес запроса");
    const method = String(options.method || "GET").toUpperCase();
    const prediction = url.pathname === "/predict" || url.pathname === "/api/predict";
    if ((prediction && method !== "POST") || (!prediction && method !== "GET")) {
      throw new Error("Этот метод не поддерживается");
    }
    const data = await snapshot();
    let result;
    if (prediction) {
      let payload;
      try { payload = typeof options.body === "string" ? JSON.parse(options.body) : options.body; }
      catch { throw new Error("Отправьте JSON с полем text"); }
      if (!payload || typeof payload !== "object" || Array.isArray(payload) || typeof payload.text !== "string") {
        throw new Error("Поле text должно быть строкой");
      }
      if (!global.BakninPagesNLP) throw new Error("Локальный анализатор недоступен. Обновите страницу.");
      result = global.BakninPagesNLP.analyze(payload.text, data.rules);
    } else if (url.pathname === "/api/universities") {
      result = ranking(data, filters(url.searchParams));
    } else if (url.pathname.startsWith("/api/universities/")) {
      let id;
      try { id = decodeURIComponent(url.pathname.slice("/api/universities/".length)); }
      catch { throw new Error("Неверный идентификатор университета"); }
      if (!Object.hasOwn(data.details, id)) throw new Error("Вуз не найден");
      result = { ...data.details[id], methodology: methodology(data) };
    } else if (url.pathname === "/api/reviews") {
      const page = integer(url.searchParams.get("page") ?? "1", "page", 1);
      const perPage = integer(url.searchParams.get("per_page") ?? "20", "per_page", 1, 100n);
      const offset = (page - 1n) * perPage;
      if (offset > maxInteger) throw new Error("page выходит за поддерживаемый диапазон");
      const rows = matchingReviews(data, filters(url.searchParams)).sort(reviewOrder);
      result = {
        items: offset >= BigInt(rows.length) ? [] : rows.slice(Number(offset), Number(offset + perPage)),
        total: rows.length, page: Number(page), per_page: Number(perPage),
        pages: Math.ceil(rows.length / Number(perPage)),
      };
    } else if (url.pathname === "/api/methodology") {
      result = methodology(data);
    } else if (url.pathname === "/health") {
      result = {
        ...data.health, status: "ok", database: "public_snapshot", model: "ready", engine: "browser_rules",
        model_version: data.rules.version + "-browser", ml_trained: false, model_diagnostic: null,
        deployment: "github_pages", exported_at: data.exported_at,
        limitation: "Опубликован снимок данных; анализ выполняется локально словарными правилами. Обученная модель не подключена.",
      };
    } else {
      throw new Error("Страница данных не найдена");
    }
    // Callers can update their own results without changing the shared snapshot.
    return clone(result);
  }

  global.BakninPagesAPI = Object.freeze({ request });
})(globalThis);
