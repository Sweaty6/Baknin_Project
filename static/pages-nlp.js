/* Local, transparent Russian rules for the GitHub Pages edition.
 * The build exports the same rule definitions used by baknin/nlp.py.
 * No text leaves the browser, and this is not a trained ML model.
 */
(function (global) {
  "use strict";

  // Python's Unicode whitespace differs slightly from JavaScript's \s/trim.
  const whitespace = "[\\t\\n\\v\\f\\r \\u001c-\\u001f\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]";
  const edgeWhitespace = new RegExp("^" + whitespace + "+|" + whitespace + "+$", "gu");
  const clauses = new RegExp("[.!?;,\\n]+|" + whitespace + "+(?:но|однако|зато)" + whitespace + "+", "iu");
  const leadingContrast = new RegExp("^(?:но|однако|зато)" + whitespace + "+", "iu");
  const boundaries = new Set(["и", "а", "но", "или", "однако", "зато"]);
  const negations = new Set(["не", "ни", "нет", "без"]);
  const compiledRules = new WeakMap();

  function normalizeText(text) {
    // Dotless i is included because Python's re.IGNORECASE [a-z] includes it.
    return (text.normalize("NFKC").toLowerCase().replace(/ё/g, "е")
      .match(/[a-zа-я0-9ı]+/gu) || []).join(" ");
  }

  function compilePattern(source) {
    // In Python, \w covers Unicode letters/numbers. JavaScript \w is ASCII.
    return new RegExp(source.replace(/\\w/g, "[\\p{L}\\p{N}_]"), "iu");
  }

  function compileRules(rules) {
    if (compiledRules.has(rules)) return compiledRules.get(rules);
    const compiled = {
      positive: compilePattern(rules.positive_pattern),
      negative: compilePattern(rules.negative_pattern),
      aspects: Object.entries(rules.aspect_patterns).map(([id, source]) => [id, compilePattern(source)]),
      stopwords: new Set(rules.stopwords),
    };
    compiledRules.set(rules, compiled);
    return compiled;
  }

  function polarities(tokens, rules) {
    const result = [];
    tokens.forEach((token, index) => {
      let value = rules.positive.test(token) ? 1 : rules.negative.test(token) ? -1 : 0;
      if (!value) return;
      let window = tokens.slice(Math.max(0, index - 3), index);
      const boundary = window.findIndex(word => boundaries.has(word));
      if (boundary !== -1) window = window.slice(boundary + 1);
      const count = window.reduce((total, word, position) => total + (
        negations.has(word) && !(word === "не" && window[position + 1] === "только") ? 1 : 0
      ), 0);
      if (count % 2) value *= -1;
      result.push([index, value]);
    });
    return result;
  }

  function sentiment(values) {
    const positive = values.some(value => value > 0);
    const negative = values.some(value => value < 0);
    return {
      sentiment: positive && negative ? "neutral" : positive ? "positive" : negative ? "negative" : "neutral",
      mixed: positive && negative,
    };
  }

  function analyze(input, ruleDefinitions) {
    if (typeof input !== "string") throw new Error("Поле text должно быть строкой");
    const text = input.replace(edgeWhitespace, "");
    if (!text || Array.from(text).length > 5000) throw new Error("Введите от 1 до 5000 символов");
    if (!/[а-яё]/iu.test(text)) throw new Error("Введите отзыв на русском языке");

    const rules = compileRules(ruleDefinitions);
    const aspectData = new Map();
    const globalPolarity = [];
    const keywords = new Map();

    for (const rawClause of text.split(clauses)) {
      // Quoted evidence remains an exact substring of the original input.
      const evidence = rawClause.replace(/^[ \t\r—–-]+|[ \t\r—–-]+$/gu, "").replace(leadingContrast, "");
      if (!evidence) continue;
      const normalized = normalizeText(evidence);
      const tokens = normalized ? normalized.split(" ") : [];
      const values = polarities(tokens, rules);
      globalPolarity.push(...values.map(([, value]) => value));

      const mentions = new Map();
      for (const [id, pattern] of rules.aspects) {
        const positions = tokens.flatMap((token, index) => pattern.test(token) ? [index] : []);
        if (!positions.length) continue;
        mentions.set(id, positions);
        if (!aspectData.has(id)) aspectData.set(id, { values: [], evidence: [] });
        const row = aspectData.get(id);
        if (!row.evidence.includes(evidence)) row.evidence.push(evidence);
      }

      for (const [position, value] of values) {
        const distances = Array.from(mentions, ([id, positions]) => [id, Math.min(...positions.map(mention => Math.abs(position - mention)))]);
        if (!distances.length) continue;
        const nearest = Math.min(...distances.map(([, distance]) => distance));
        for (const [id, distance] of distances) {
          if (distance === nearest) aspectData.get(id).values.push(value);
        }
      }

      const important = new Set(Array.from(mentions.values()).flat());
      values.forEach(([index]) => important.add(index));
      tokens.forEach((token, index) => {
        if (token.length >= 3 && !rules.stopwords.has(token)) {
          keywords.set(token, (keywords.get(token) || 0) + (important.has(index) ? 3 : 1));
        }
      });
    }

    const result = sentiment(globalPolarity);
    result.aspects = Object.entries(ruleDefinitions.aspect_labels).flatMap(([id, label]) => {
      const row = aspectData.get(id);
      return row ? [{ id, label, sentiment: sentiment(row.values).sentiment, confidence: null, evidence: row.evidence.slice(0, 5) }] : [];
    });
    result.keywords = Array.from(keywords.keys()).sort((left, right) => (
      keywords.get(right) - keywords.get(left) || (left < right ? -1 : left > right ? 1 : 0)
    )).slice(0, 10);
    result.confidence = null;
    result.confidence_threshold = null;
    result.needs_review = true;
    result.engine = "browser_rules";
    result.model_version = ruleDefinitions.version + "-browser";
    result.explanation = "Текст обработан локально в браузере словарными правилами с учетом отрицаний; на сервер он не отправляется. "
      + "Обученная модель не подключена. Правила не имеют измеренной вероятности, поэтому уверенность не указана и результат требует проверки.";
    if (!globalPolarity.length) result.explanation += " Оценочные слова не найдены: нейтральная метка требует проверки.";
    if (result.mixed) result.explanation += " Найдены положительные и отрицательные оценки: требуется проверка.";
    return result;
  }

  global.BakninPagesNLP = Object.freeze({ analyze });
})(globalThis);
